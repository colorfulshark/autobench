"""Standalone remote helper. Keep compatible with Python 3.8 and stdlib only.

Control operations print a single JSON response. The worker is a detached systemd
service; durable job files, rather than the SSH channel, are the source of truth.
"""

import datetime
import fcntl
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath

TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temp), str(path))
    directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def managed(argv):
    return argv if os.geteuid() == 0 else ["sudo", "-n"] + argv


def command(argv, privileged=False, timeout=15, env=None):
    result = subprocess.run(
        managed(argv) if privileged else argv,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        env=env,
    )
    if result.returncode:
        raise RuntimeError("command failed: %s: %s" % (argv[0], result.stderr.strip()))
    return result.stdout


def efi():
    text = command(["efibootmgr"], privileged=True)
    result = {"raw": text, "entries": {}}
    for line in text.splitlines():
        match = re.match(r"Boot(Current|Next):\s*([0-9a-fA-F]{4})", line)
        if match:
            result[match.group(1).lower()] = match.group(2).upper()
        match = re.match(r"Boot([0-9a-fA-F]{4})(\*?)\s+(.*)", line)
        if match:
            result["entries"][match.group(1).upper()] = {
                "active": bool(match.group(2)),
                "label": match.group(3),
            }
    return result


def grub_tool(name):
    # An unprivileged SSH login may omit sbin from PATH, even with passwordless sudo.
    system_path = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin"
    for prefix in ("grub-", "grub2-"):
        executable = shutil.which(prefix + name, path=system_path)
        if executable:
            return executable
    raise RuntimeError("missing GRUB utility: grub-" + name + " or grub2-" + name)


def boot_file(path):
    return command(["cat", "--", str(path)], privileged=True)


def path_on_boot_filesystem(path):
    target, root = command(
        ["findmnt", "-nro", "TARGET,FSROOT", "--target", str(path)], privileged=True
    ).split()
    return PurePosixPath(root) / path.relative_to(target)


def grub_loaders():
    verbose = command(["efibootmgr", "-v"], privileged=True)
    loaders = {}
    for line in verbose.splitlines():
        loader = re.match(
            r"Boot([0-9A-F]{4})\*?\s+.*?HD\(\d+,GPT,([a-fA-F0-9-]+),[^)]*\)/File\(([^)]+)\)",
            line,
        )
        if not loader:
            continue
        number, partition, file = loader.groups()
        file = PurePosixPath(file.replace("\\", "/").lstrip("/"))
        if ".." not in file.parts and re.fullmatch(r"(?:shim|grub)[\w-]*\.efi", file.name, re.I):
            loaders[number] = partition, file
    return loaders


def grub_config(boot_entry, loaders=None):
    """Find the menu for an explicit loader, including its ESP forwarding stub."""
    loaders = grub_loaders() if loaders is None else loaders
    if boot_entry not in loaders:
        raise RuntimeError("cannot locate the GRUB loader for Boot" + boot_entry)
    partition, file = loaders[boot_entry]
    for mount in (Path("/boot/efi"), Path("/efi"), Path("/boot")):
        try:
            source = command(["findmnt", "-nro", "SOURCE", "--mountpoint", str(mount)]).strip()
            uuid = command(["lsblk", "-ndo", "PARTUUID", "--", source], privileged=True).strip()
        except (OSError, RuntimeError):
            continue
        if uuid.lower() == partition.lower():
            break
    else:
        raise RuntimeError("the EFI system partition for Boot" + boot_entry + " is not mounted")
    stub_path = mount.joinpath(*file.parent.parts, "grub.cfg")
    stub = boot_file(stub_path)
    if re.search(r"(?m)^\s*(?:menuentry|blscfg|load_env)\b", stub):
        return stub_path, stub
    lines = [shlex.split(line, comments=True) for line in stub.splitlines()]
    search = next(
        (
            line
            for line in lines
            if line and (line[0] == "search.fs_uuid" or line[0] == "search" and "--fs-uuid" in line)
        ),
        [],
    )
    uuid = next((word for word in search[1:] if re.fullmatch(r"[0-9a-fA-F-]{8,}", word)), None)
    prefix = next(
        (
            re.fullmatch(r"prefix=\(\$(?:\w+|\{\w+\})\)(/[^ ]*)", line[1])
            for line in lines
            if len(line) == 2 and line[0] == "set" and line[1].startswith("prefix=")
        ),
        None,
    )
    if (
        not uuid
        or not prefix
        or not any(
            line in (["configfile", "$prefix/grub.cfg"], ["configfile", "${prefix}/grub.cfg"])
            for line in lines
        )
    ):
        raise RuntimeError("unsupported GRUB forwarding configuration")
    probe_tool = grub_tool("probe")
    for candidate in (Path("/boot/grub/grub.cfg"), Path("/boot/grub2/grub.cfg")):
        try:
            fs = command([probe_tool, "--target=fs_uuid", str(candidate)], privileged=True).strip()
            relative = path_on_boot_filesystem(candidate).parent
            if fs.lower() == uuid.lower() and relative == PurePosixPath(prefix.group(1)):
                return candidate, boot_file(candidate)
        except (OSError, RuntimeError, ValueError):
            continue
    raise RuntimeError("the active GRUB menu is not available in /boot/grub or /boot/grub2")


def grub_env(tool, path):
    return dict(
        line.split("=", 1)
        for line in command([tool, str(path), "list"], privileged=True).splitlines()
        if "=" in line
    )


def menu_id(args):
    for index, arg in enumerate(args):
        if arg.startswith("--id="):
            return arg[5:]
        if arg in ("--id", "$menuentry_id_option", "${menuentry_id_option}"):
            return args[index + 1] if index + 1 < len(args) else None
    return None


def grub_entries(config, path, environment):
    root = {"children": []}
    blocks = [root]
    bls_loaded = False
    for line in config.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("menuentry ", "submenu ")) and line.endswith("{"):
            kind, label, *args = shlex.split(line[:-1], comments=True)
            entry = {"label": label, "id": menu_id(args), "children": []}
            if kind == "menuentry":
                entry["linux"] = []
                entry["initrd"] = []
                entry["boot_sources"] = set()
            blocks[-1].get("children", []).append(entry)
            blocks.append(entry)
        elif line == "}":
            if len(blocks) > 1:
                blocks.pop()
        elif line.endswith(" {"):
            blocks.append({})
        elif "linux" in blocks[-1] and re.match(r"linux(?:efi|16)?\s", line):
            blocks[-1]["linux"].append(shlex.split(line, comments=True)[1:])
        elif "initrd" in blocks[-1] and re.match(r"initrd(?:efi|16)?\s", line):
            blocks[-1]["initrd"].append(shlex.split(line, comments=True)[1:])
        elif "boot_sources" in blocks[-1] and line.startswith(("search ", "search.fs_uuid ")):
            parts = shlex.split(line, comments=True)
            if "--set=root" in parts or parts[0] == "search.fs_uuid" and "root" in parts:
                blocks[-1]["boot_sources"].update(
                    word.lower() for word in parts[1:] if re.fullmatch(r"[0-9a-fA-F-]{8,}", word)
                )
        elif "linux" in blocks[-1] and re.match(
            r"(?:chainloader|configfile|multiboot2?|source)\s", line
        ):
            blocks[-1]["linux"].append([])
        elif line == "blscfg":
            if bls_loaded or blocks[-1] is not root:
                raise RuntimeError("unsupported placement of GRUB BLS loader")
            root["children"].extend(bls_entries(path, environment))
            bls_loaded = True
    return root["children"]


def bls_entries(path, environment):
    # grubby uses the same version ordering as Fedora's blscfg. The fragments
    # supply the original options, which grubby formats into separate fields.
    boot = path.parent.parent if path.parent.name in ("grub", "grub2") else Path("/boot")
    directory = boot / environment.get("blsdir", "loader/entries").lstrip("/")
    if ".." in directory.parts:
        raise RuntimeError("invalid GRUB BLS directory")
    output = command(["grubby", "--info=ALL", "--bls-directory", str(directory)], privileged=True)
    entries = []
    for match in re.finditer(r'(?m)^id="?([\w.+-]+)"?\s*$', output):
        id_ = match.group(1)
        fields = {}
        for line in boot_file(directory / (id_ + ".conf")).splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                fields.setdefault(parts[0], []).append(parts[1])
        if not fields.get("title") or not fields.get("linux"):
            raise RuntimeError("incomplete GRUB BLS entry: " + id_)
        entries.append(
            {
                "id": id_,
                "label": fields["title"][0],
                "linux": [
                    [kernel, *shlex.split(" ".join(fields.get("options", [])))]
                    for kernel in fields["linux"]
                ],
                "initrd": [fields["initrd"]] if fields.get("initrd") else [],
                "children": [],
            }
        )
    if not entries:
        raise RuntimeError("grubby did not return GRUB BLS entries")
    return entries


def grub_words(words, variables):
    result = []
    for word in words:
        variable = re.fullmatch(r"\$(?:\{(\w+)\}|(\w+))", word)
        name = (variable.group(1) or variable.group(2)) if variable else None
        if name in variables:
            result.extend(shlex.split(variables[name]))
        elif "$" in word or "`" in word:
            raise ValueError("unresolved GRUB variable")
        else:
            result.append(word)
    return result


def kernel_argument_groups(args, vt_handoff=False):
    roots = [arg for arg in args if arg.startswith(("root=", "rootflags="))]
    remaining = [
        arg
        for arg in args
        if not arg.startswith(("root=", "rootflags=", "BOOT_IMAGE=", "initrd="))
        and not (vt_handoff and arg == "vt.handoff=7")
    ]
    return roots, remaining


def bootloader_cmdline(release):
    actual = shlex.split(Path("/proc/cmdline").read_text())
    if not re.fullmatch(r"x86_64|i[3-6]86", platform.machine()):
        return actual
    try:
        config = Path("/boot/config-" + release).read_text()
        if "CONFIG_CMDLINE_BOOL=y" not in config.splitlines():
            return actual
        if "CONFIG_CMDLINE_OVERRIDE=y" in config.splitlines():
            return actual
        setting = re.search(r'(?m)^CONFIG_CMDLINE=(".*")$', config)
        built_in = shlex.split(shlex.split(setting.group(1))[0]) if setting else []
    except (OSError, ValueError):
        return actual
    # x86 prepends built-in arguments. Other differences must still mismatch.
    return actual[len(built_in) :] if actual[: len(built_in)] == built_in else actual


def grub_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def grub_leaves(menu, variables, parents=()):
    for index, entry in enumerate(menu):
        level = (entry["id"], entry["label"], str(index))
        ancestry = (*parents, level)
        if "linux" not in entry:
            yield from grub_leaves(entry["children"], variables, ancestry)
            continue
        id_ = ">".join(id_ or index for id_, _, index in ancestry)
        leaf = {
            "id": id_,
            "label": " › ".join(label for _, label, _ in ancestry),
            "selectors": ancestry,
            "recovery": any(
                re.search(r"recovery|rescue|single.user", name or "", re.I)
                for id_, label, _ in ancestry
                for name in (id_, label)
            ),
        }
        kernels = entry["linux"]
        initrds = entry["initrd"]
        if len(kernels) == 1 and kernels[0] and len(initrds) <= 1:
            try:
                kernel, *args = grub_words(kernels[0], variables)
                initrd = grub_words(initrds[0], variables) if initrds else []
                if kernel.startswith("/") and any(arg.startswith("root=") for arg in args):
                    leaf["signature"] = grub_digest(
                        [kernel, args, initrd, sorted(entry.get("boot_sources", ()))]
                    )
                    leaf["kernel"] = kernel
                    leaf["vt_handoff"] = any(
                        arg in ("$vt_handoff", "${vt_handoff}") for arg in kernels[0]
                    )
                    leaf["roots"], leaf["args"] = kernel_argument_groups(args)
            except ValueError:
                pass
        yield leaf


def resolve_grub_entry(entries, selector):
    if not selector:
        return None
    parts = selector.split(">")
    matches = [
        item
        for item in entries
        if len(item["selectors"]) == len(parts)
        and all(part in names for part, names in zip(parts, item["selectors"]))
    ]
    return matches[0] if len(matches) == 1 else None


def grub_boot_menu(boot_entry, loaders=None):
    path, config = grub_config(boot_entry, loaders)
    env_file = Path(
        command(["realpath", "-e", "--", str(path.with_name("grubenv"))], privileged=True).strip()
    )
    tool = grub_tool("editenv")
    environment = grub_env(tool, env_file)
    variables = {"vt_handoff": "", "tuned_params": "", "tuned_initrd": ""}
    for match in re.finditer(r"(?m)^\s*set\s+(kernelopts|tuned_params|tuned_initrd)=(.+)$", config):
        values = shlex.split(match.group(2), comments=True)
        if values:
            variables[match.group(1)] = values[0]
    variables.update(
        {name: environment[name] for name in (*variables, "kernelopts") if name in environment}
    )
    if re.search(r"(?m)^\s*source\s+[^\n]*[/]tuned\.cfg", config):
        try:
            tuned = boot_file(path.with_name("tuned.cfg"))
        except RuntimeError:
            tuned = ""
        for match in re.finditer(r"(?m)^\s*set\s+(tuned_params|tuned_initrd)=(.+)$", tuned):
            values = shlex.split(match.group(2), comments=True)
            if values:
                variables[match.group(1)] = values[0]
    entries = list(grub_leaves(grub_entries(config, path, environment), variables))
    if not entries:
        raise RuntimeError("no GRUB menu entries found")
    release = platform.release()
    actual = bootloader_cmdline(release)
    image = next((arg[11:] for arg in actual if arg.startswith("BOOT_IMAGE=")), None)
    if image:
        image = re.sub(r"^\([^)]*\)", "", image)
    for entry in entries:
        if "signature" not in entry:
            continue
        roots, args = kernel_argument_groups(actual, entry["vt_handoff"])
        entry["same_os"] = entry["roots"] == roots
        image_matches = (
            entry["kernel"] == image
            if image
            else PurePosixPath(entry["kernel"]).name in {release, "vmlinuz-" + release}
        )
        entry["matches_boot"] = entry["same_os"] and image_matches and entry["args"] == args

    # Entries that look identical from Linux can only be verified if their
    # kernel, argument and initrd commands describe the same boot.
    observable = {}
    for entry in entries:
        if "signature" in entry:
            key = (PurePosixPath(entry["kernel"]).name, tuple(entry["roots"]), tuple(entry["args"]))
            observable.setdefault(key, set()).add(entry["signature"])
    for entry in entries:
        key = (
            PurePosixPath(entry.get("kernel", "")).name,
            tuple(entry.get("roots", [])),
            tuple(entry.get("args", [])),
        )
        entry["selectable"] = (
            len(observable.get(key, ())) == 1 and resolve_grub_entry(entries, entry["id"]) is entry
        )
    # A pending one-time selection is independent of the persistent default.
    header = re.split(r"(?m)^\s*(?:menuentry|submenu|blscfg)\b", config, maxsplit=1)[0]
    defaults = {
        value.strip("'\"") for value in re.findall(r"(?m)^\s*set\s+default=(.+)$", header)
    } - {"$next_entry", "${next_entry}"}
    selector = next(iter(defaults)) if len(defaults) == 1 else ("0" if not defaults else None)
    if selector in ("$saved_entry", "${saved_entry}"):
        selector = environment.get("saved_entry") or "0"
    default = resolve_grub_entry(entries, selector)
    if not (default and default.get("same_os") and default["selectable"]):
        normal = [
            entry
            for entry in entries
            if entry["selectable"] and entry.get("same_os") and not entry["recovery"]
        ]
        running = [entry for entry in normal if entry.get("matches_boot")]
        top_level = [entry for entry in running if len(entry["selectors"]) == 1]
        candidates = top_level or running or normal
        default = candidates[0] if len(candidates) == 1 else None
    fs_probe = grub_tool("probe")
    filesystem = command([fs_probe, "--target=fs", str(env_file)], privileged=True).strip()
    abstraction = command(
        [fs_probe, "--target=abstraction", str(env_file)], privileged=True
    ).strip()
    uuid = command([fs_probe, "--target=fs_uuid", str(env_file)], privileged=True).strip()
    identity = [uuid.lower(), str(path_on_boot_filesystem(env_file))]
    next_entry = environment.get("next_entry") or None
    previous_entry = environment.get("prev_saved_entry") or None
    environment_ref = r"""["']?\$\{?(?:env_block|prefix|config_directory)\}?(?:/grubenv)?["']?"""
    one_shot = (
        bool(uuid)
        and not abstraction
        and (
            filesystem in ("ext2", "xfs", "fat")
            or filesystem == "btrfs"
            and bool(environment.get("env_block"))
        )
        and bool(re.search(r"(?m)^\s*set\s+default=[\"']?\$\{?next_entry\}?[\"']?\s*$", header))
        and bool(re.search(r"(?m)^\s*set\s+next_entry=(?:\"\"|'')?\s*$", header))
        and bool(re.search(rf"(?m)^\s*load_env(?:[ \t]+-f[ \t]+{environment_ref})?[ \t]*$", header))
        and bool(
            re.search(rf"(?m)^\s*save_env\s+(?:-f\s+{environment_ref}\s+)?next_entry\s*$", header)
        )
    )
    return (
        {
            "boot_entry": boot_entry,
            "entries": [
                {
                    key: entry[key]
                    for key in (
                        "id",
                        "label",
                        "selectors",
                        "selectable",
                        "signature",
                        "matches_boot",
                    )
                    if key in entry
                }
                for entry in entries
            ],
            "default_entry": default["id"]
            if default and default["selectable"] and one_shot
            else None,
            "identity": identity,
            "next_entry": next_entry,
            "prev_saved_entry": previous_entry,
            "one_shot": one_shot,
        },
        tool,
        env_file,
    )


def current_grub_menu(firmware):
    current = firmware.get("current")
    loaders = grub_loaders()
    try:
        return grub_boot_menu(current, loaders)[0]
    except (OSError, RuntimeError, ValueError) as current_error:
        # Firmware disk entries can boot a GRUB installation without naming its
        # EFI file. Find the installed menu whose entry describes this boot.
        matches = {}
        for number in loaders:
            if number == current or not firmware["entries"].get(number, {}).get("active"):
                continue
            try:
                menu, _, _ = grub_boot_menu(number, loaders)
            except (OSError, RuntimeError, ValueError):
                continue
            if any(entry.get("matches_boot") for entry in menu["entries"]):
                key = (
                    tuple(menu["identity"]),
                    tuple((entry["id"], entry.get("signature")) for entry in menu["entries"]),
                )
                matches.setdefault(key, menu)
        if len(matches) == 1:
            return next(iter(matches.values()))
        if matches:
            raise RuntimeError("multiple GRUB menus match the running system") from current_error
        raise


def probe(payload):
    result = {
        "schema_version": 1,
        "python": list(sys.version_info[:3]),
        "python_executable": sys.executable,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "home": str(Path.home()),
        "boot_id": boot_id(),
        "hostname": platform.node(),
        "commands": {name: shutil.which(name) for name in payload.get("requirements", [])},
    }
    try:
        result["efi"] = efi()
    except Exception as exc:
        result["efi_error"] = str(exc)
    firmware = result.get("efi", {})
    grub_boot_entry = payload.get("grub_boot_entry")
    if payload.get("grub") and firmware.get("current"):
        try:
            if grub_boot_entry is None:
                result["grub"] = current_grub_menu(firmware)
            else:
                if not re.fullmatch(r"[0-9A-F]{4}", grub_boot_entry):
                    raise ValueError("invalid UEFI entry for GRUB discovery")
                if grub_boot_entry not in firmware.get("entries", {}):
                    raise RuntimeError("Boot" + grub_boot_entry + " is missing")
                result["grub"], _, _ = grub_boot_menu(grub_boot_entry)
        except Exception as exc:
            result["grub_error"] = str(exc)
    try:
        result["systemd_version"] = int(command(["systemctl", "--version"]).split()[1])
        command(["systemctl", "show", "--property=Version"], privileged=True)
        result["systemd_available"] = True
    except Exception as exc:
        result["systemd_error"] = str(exc)
    result["disk_free"] = shutil.disk_usage(str(Path.home())).free
    return result


def job_path(payload):
    path = Path(payload["job_dir"])
    if not path.is_absolute() or path.name != payload["job_id"]:
        raise ValueError("invalid absolute job path")
    if not re.fullmatch(r"[0-9a-f]{32}", payload["job_id"]):
        raise ValueError("invalid job id")
    return path


def lock(path, nonblocking=True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "a+")


def stage(payload):
    directory = job_path(payload)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(directory / "dispatch.lock"):
        path = directory / "manifest.json"
        existing = read_json(path)
        if existing is not None and existing != payload["manifest"]:
            raise RuntimeError("immutable job manifest mismatch")
        if existing is None:
            atomic_json(path, payload["manifest"])
        (directory / "artifacts").mkdir(mode=0o700, exist_ok=True)
    source = Path(payload["manifest"]["source_path"])
    return {
        "staged": True,
        "source_valid": source.is_file() and digest(source) == payload["manifest"]["source_sha256"],
    }


def service(directory):
    unit = "autobench-" + directory.name + ".service"
    # Explicit properties also work after the transient unit was garbage collected.
    result = subprocess.run(
        [
            "systemctl",
            "show",
            unit,
            "--no-pager",
            "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if "LoadState" not in properties or "ActiveState" not in properties:
        raise RuntimeError("systemd service status is unavailable")
    return properties


def file_inventory(artifacts):
    files = []
    for current, dirs, names in os.walk(str(artifacts), followlinks=False):
        dirs.sort()
        for name in dirs:
            if (Path(current) / name).is_symlink():
                raise RuntimeError("artifact directory symlink is not allowed")
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("artifact must be a regular file")
            files.append(
                {
                    "path": str(path.relative_to(artifacts)),
                    "size": path.stat().st_size,
                    "sha256": digest(path),
                }
            )
    return {"schema_version": 1, "files": files}


def build_path(directory, manifest):
    build_id = manifest["build_id"]
    if not re.fullmatch(r"[0-9a-f]{32}", build_id):
        raise ValueError("invalid build id")
    return directory.parent.parent / "builds" / build_id


def seal_build(directory, manifest, state):
    """Also preserve partial build evidence when systemd killed a worker."""
    build = build_path(directory, manifest)
    if (build / "artifacts.json").exists() or manifest["build_id"] != directory.name:
        return
    artifacts = build / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = read_json(
        artifacts / "build.json",
        {
            "schema_version": 2,
            "identity": build_identity(manifest),
            "command": {},
        },
    )
    record.update({"status": state["status"], "finished": now(), "reason": state.get("reason")})
    atomic_json(artifacts / "build.json", record)
    atomic_json(build / "artifacts.json", file_inventory(artifacts))


def finalize(directory, state):
    """Seal inventories only after the process tree is no longer writing."""
    # Also clean up when status() reconciles a worker killed or interrupted by reboot.
    shutil.rmtree(directory / "warmup", ignore_errors=True)
    warmup = state.get("warmup")
    if warmup and warmup["status"] not in TERMINAL and state["status"] in TERMINAL:
        warmup.update(
            {
                "status": state["status"],
                "finished": state.get("finished", now()),
                "reason": state.get("reason"),
            }
        )
    artifacts = directory / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    manifest = read_json(directory / "manifest.json")
    seal_build(directory, manifest, state)
    atomic_json(directory / "state.json", state)
    atomic_json(directory / "artifacts.json", file_inventory(artifacts))


def terminal_status(directory, manifest, state):
    build = build_path(directory, manifest)
    return {
        "phase": "terminal",
        "state": state,
        "inventory": read_json(directory / "artifacts.json"),
        "common": {"directory": str(build), "inventory": read_json(build / "artifacts.json")},
    }


def status(payload):
    directory = job_path(payload)
    manifest = read_json(directory / "manifest.json")
    if manifest is None:
        return {"phase": "absent"}
    with lock(directory / "dispatch.lock"):
        state = read_json(directory / "state.json", {})
        unit = service(directory)
        active = unit.get("ActiveState") in ("active", "activating", "deactivating", "reloading")
        inventory = read_json(directory / "artifacts.json")
        # A completed worker may still be returning to systemd; wait for quiescence.
        if inventory and not active:
            return terminal_status(directory, manifest, state)
        if manifest["boot_id"] != boot_id():
            if active:
                return {"phase": "uncertain", "message": "unexpected active unit after boot change"}
            state = {
                **state,
                "status": "interrupted",
                "reason": "remote boot ID changed",
                "finished": now(),
                "boot_id": manifest["boot_id"],
            }
            finalize(directory, state)
        elif active:
            return {"phase": "active", "state": state, "service": unit}
        elif state.get("status") in TERMINAL:
            # Reconcile a worker killed during the final inventory write.
            finalize(directory, state)
        elif unit.get("LoadState") == "loaded" or unit.get("ActiveState") == "failed":
            state = {
                **state,
                "status": "interrupted",
                "reason": "service exited without final state",
                "finished": now(),
                "service": unit,
                "boot_id": manifest["boot_id"],
            }
            finalize(directory, state)
        elif read_json(directory / "submitted.json"):
            return {"phase": "uncertain", "message": "submission recorded but unit/state is absent"}
        else:
            return {"phase": "staged"}
        return terminal_status(directory, manifest, state)


def start(payload):
    directory = job_path(payload)
    with lock(directory / "dispatch.lock"):
        manifest = read_json(directory / "manifest.json")
        if manifest is None:
            raise RuntimeError("job is not staged")
        if (directory / "submitted.json").exists() or (directory / "state.json").exists():
            return {"submitted": False, "existing": True}
        if service(directory).get("LoadState") != "not-found":
            return {"submitted": False, "existing": True}
        if boot_id() != manifest["boot_id"]:
            raise RuntimeError("boot ID changed before submission")
        # Reserve the OS until local collection is acknowledged, including between jobs.
        root = directory.parent.parent
        with lock(root / "reservation.lock"):
            owner = read_json(root / "reservation.json")
            if owner and owner["job_id"] != directory.name:
                raise RuntimeError("another job owns this OS: " + owner["job_id"])
            atomic_json(root / "reservation.json", {"job_id": directory.name})
        atomic_json(directory / "submitted.json", {"time": now(), "boot_id": boot_id()})
        argv = [
            "systemd-run",
            "--unit=autobench-" + directory.name,
            "--property=Type=exec",
            "--property=Restart=no",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=30s",
            "--uid=" + str(os.getuid()),
            "--gid=" + str(os.getgid()),
            sys.executable,
            str(directory / "runner.py"),
            "worker",
            str(directory / "manifest.json"),
        ]
        # systemd expands dollars in ExecStart arguments even without a shell.
        argv[-4:] = [arg.replace("$", "$$") for arg in argv[-4:]]
        try:
            command(argv, privileged=True, timeout=30)
        except subprocess.TimeoutExpired:
            # It may have reached systemd. Preserve the marker and reconcile; never resubmit.
            return {"submitted": True, "uncertain": True}
        except Exception as exc:
            # Check the unit before classifying a failed systemd-run response.
            unit = service(directory)
            if unit.get("ActiveState") not in ("active", "activating", "deactivating"):
                finalize(
                    directory,
                    {
                        "status": "failed",
                        "reason": str(exc),
                        "finished": now(),
                        "boot_id": manifest["boot_id"],
                    },
                )
        return {"submitted": True}


def acknowledge(payload):
    directory = job_path(payload)
    manifest = read_json(directory / "manifest.json")
    if (
        not (directory / "artifacts.json").exists()
        or not (build_path(directory, manifest) / "artifacts.json").exists()
    ):
        raise RuntimeError("cannot acknowledge an unsealed job")
    root = directory.parent.parent
    with lock(root / "reservation.lock"):
        owner = read_json(root / "reservation.json")
        if owner and owner["job_id"] == directory.name:
            (root / "reservation.json").unlink()
        atomic_json(directory / "collected.json", {"time": now()})
    return {"acknowledged": True}


def cancel(payload):
    directory = job_path(payload)
    if not (directory / "manifest.json").exists():
        return {"phase": "absent"}
    with lock(directory / "dispatch.lock"):
        unit = service(directory)
        if unit.get("LoadState") == "loaded":
            command(
                ["systemctl", "stop", "autobench-" + directory.name + ".service"],
                privileged=True,
                timeout=45,
            )
        state = read_json(directory / "state.json", {})
        if state.get("status") not in TERMINAL:
            finalize(
                directory,
                {
                    **state,
                    "status": "cancelled",
                    "finished": now(),
                    "boot_id": boot_id(),
                },
            )
    return status(payload)


def switch(payload):
    root = Path(payload["root"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root / "reservation.lock"):
        if read_json(root / "reservation.json"):
            raise RuntimeError("uncollected job prevents a system switch")
        if boot_id() != payload["previous_boot_id"]:
            raise RuntimeError("boot ID changed before reboot request")
        target = payload["boot_entry"]
        if not re.fullmatch(r"[0-9A-F]{4}", target):
            raise ValueError("invalid boot entry")
        current = efi()
        if target not in current["entries"] or not current["entries"][target]["active"]:
            raise RuntimeError("target EFI entry is missing or inactive")
        if current.get("next"):
            raise RuntimeError("existing BootNext prevents reboot")
        grub_target = payload.get("grub")
        if grub_target:
            # The firmware may have reached the same installation through a disk
            # entry. Resolve the requested loader independently before writing.
            menu, tool, env_file = grub_boot_menu(target)
            selected = resolve_grub_entry(menu["entries"], grub_target["id"])
            if (
                not menu["one_shot"]
                or menu["identity"] != grub_target["identity"]
                or not selected
                or not selected["selectable"]
                or selected["signature"] != grub_target["signature"]
            ):
                raise RuntimeError("GRUB menu or target changed before reboot request")
            if menu["next_entry"] or menu["prev_saved_entry"]:
                raise RuntimeError("existing GRUB boot selection prevents reboot")
        marker = root / "switches" / (payload["token"] + ".json")
        if marker.exists():
            raise RuntimeError("reboot was already submitted; reconcile instead of resubmitting")
        intent = {"target": target, "boot_id": boot_id(), "time": now()}
        if grub_target:
            intent["grub"] = grub_target
        atomic_json(marker, intent)
        efi_attempted = False
        try:
            if grub_target:
                selector = selected["id"]
                command([tool, str(env_file), "set", "next_entry=" + selector], privileged=True)
                if grub_env(tool, env_file).get("next_entry") != selector:
                    raise RuntimeError("GRUB next_entry readback mismatch")
            efi_attempted = True
            command(["efibootmgr", "-n", target], privileged=True)
            if efi().get("next") != target:
                raise RuntimeError("BootNext readback mismatch")
            if grub_target:
                try:
                    command(["systemctl", "reboot", "--no-block"], privileged=True)
                except subprocess.TimeoutExpired:
                    # The request may already have reached systemd. Reconcile by boot ID.
                    return {"reboot_requested": True}
        except Exception as exc:
            if not grub_target:
                raise
            rollback_errors = []
            if efi_attempted:
                try:
                    if efi().get("next") == target:
                        command(["efibootmgr", "-N"], privileged=True)
                        if efi().get("next") == target:
                            raise RuntimeError("BootNext is still set")
                except Exception as rollback:
                    rollback_errors.append(str(rollback))
            try:
                if grub_env(tool, env_file).get("next_entry") == selector:
                    command([tool, str(env_file), "unset", "next_entry"], privileged=True)
                    if grub_env(tool, env_file).get("next_entry") == selector:
                        raise RuntimeError("GRUB next_entry is still set")
            except Exception as rollback:
                rollback_errors.append(str(rollback))
            if rollback_errors:
                raise RuntimeError(f"{exc}. Boot rollback failed: " + ", ".join(rollback_errors))
            raise
        if not grub_target:
            command(["systemctl", "reboot", "--no-block"], privileged=True)
    return {"reboot_requested": True}


def extract_source(source, destination):
    """No links/devices and no traversal; works without Python 3.12 tar filters."""
    destination.mkdir(mode=0o700, exist_ok=True)
    with tarfile.open(str(source), "r:*") as archive:
        seen = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
                raise ValueError("unsafe archive member: " + member.name)
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                if str(path) in seen:
                    raise ValueError("duplicate archive file")
                seen.add(str(path))
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(0o700 if member.mode & 0o111 else 0o600)


class WorkerCancelled(BaseException):
    """Escape optional toolchain probe handlers, just as KeyboardInterrupt does."""


def cancellation_signal(signum, frame):
    raise WorkerCancelled("worker received signal %s" % signum)


def run_process(argv, cwd, env, timeout, stdout, stderr, record):
    record.update({"argv": argv, "cwd": str(cwd), "started": now(), "timeout": timeout})
    process = None
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                record["exit_code"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                raise RuntimeError("command exceeded its timeout")
            if record["exit_code"] != 0:
                raise RuntimeError("command exited with code %s" % record["exit_code"])
    finally:
        if process is not None:
            # Always remove the whole process group, even if its leader already exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            record["exit_code"] = process.wait()
        record["finished"] = now()


def build_identity(manifest):
    return {
        key: manifest[key]
        for key in (
            "build_id",
            "boot_id",
            "source_sha256",
            "system_env",
            "benchmark_env",
            "execution",
        )
    }


def verify_build(build):
    ready = read_json(build / "ready.json")
    if ready is None or ready["identity"]["boot_id"] != boot_id():
        raise RuntimeError("shared build is not ready or its boot ID changed; rebuild is disabled")
    cwd = build / "source" / ready["identity"]["execution"]["source_subdir"]
    binary_dir = cwd / "pgms"
    if binary_dir.is_symlink() or not binary_dir.is_dir():
        raise RuntimeError("shared binary directory is missing or unsafe")
    expected = ready["binaries"]
    if file_inventory(binary_dir) != expected:
        raise RuntimeError("shared binaries changed; automatic rebuild is disabled")
    return cwd


def configured_environment(manifest):
    env = dict(os.environ)
    env.update(manifest["system_env"])
    env.update(manifest["benchmark_env"])
    return env


def runtime_environment(manifest, build, directory):
    env = configured_environment(manifest)
    env["UB_BINDIR"] = str(build / "source" / manifest["execution"]["source_subdir"] / "pgms")
    # Upstream Run passes MAKE through a shell, so quote the complete command.
    env["MAKE"] = shlex.join([sys.executable, str(build / "runner.py"), "check-build", str(build)])
    env["UB_RESULTDIR"] = str(directory / "artifacts" / manifest["execution"]["artifact_dir"])
    env.setdefault("UB_TMPDIR", str(directory / "work" / "tmp"))
    return env


def prepare_build(directory, manifest, state):
    build = build_path(directory, manifest)
    identity = build_identity(manifest)
    if (build / "ready.json").exists():
        if read_json(build / "ready.json")["identity"] != identity:
            raise RuntimeError("shared build identity changed; automatic rebuild is disabled")
        if not (build / "artifacts.json").exists():
            raise RuntimeError("shared build artifacts are not sealed")
        return build, verify_build(build)
    if manifest["build_id"] != directory.name or (build / "artifacts" / "build.json").exists():
        raise RuntimeError("shared build missing or incomplete; automatic rebuild is disabled")
    artifacts = build / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {
        "schema_version": 2,
        "identity": identity,
        "status": "building",
        "started": now(),
        "command": {},
        "tool": manifest.get("tool"),
    }
    atomic_json(artifacts / "build.json", record)
    state["status"] = "building"
    atomic_json(directory / "state.json", state)
    try:
        source = Path(manifest["source_path"])
        if digest(source) != manifest["source_sha256"]:
            raise RuntimeError("source archive checksum mismatch")
        extract_source(source, build / "source")
        subdir = PurePosixPath(manifest["execution"]["source_subdir"])
        if subdir.is_absolute() or ".." in subdir.parts:
            raise ValueError("invalid source directory")
        cwd = (build / "source").joinpath(*subdir.parts)
        build_env = configured_environment(manifest)
        toolchain = {}
        execution = manifest["execution"]
        for name, argv in {
            "compiler": execution["compiler_argv"] + ["--version"],
            "make": ["make", "--version"],
        }.items():
            try:
                toolchain[name] = {"argv": argv, "output": command(argv, timeout=5, env=build_env)}
            except Exception as exc:
                toolchain[name] = {"argv": argv, "error": str(exc)}
        atomic_json(artifacts / "toolchain.json", toolchain)
        atomic_json(artifacts / "build-environment.json", build_env)
        record["command"] = {
            "argv": execution["build_argv"],
            "cwd": str(cwd),
            "started": now(),
            "timeout": execution["build_timeout"],
        }
        atomic_json(artifacts / "build.json", record)
        run_process(
            execution["build_argv"],
            cwd,
            build_env,
            execution["build_timeout"],
            artifacts / "build.stdout",
            artifacts / "build.stderr",
            record["command"],
        )
        if not (cwd / "pgms").is_dir() or (cwd / "pgms").is_symlink():
            raise RuntimeError("build did not produce a pgms directory")
        binaries = file_inventory(cwd / "pgms")
        if not binaries["files"]:
            raise RuntimeError("build did not produce any programs")
        shutil.copyfile(__file__, build / "runner.py")
        record.update({"status": "succeeded", "finished": now()})
        atomic_json(artifacts / "build.json", record)
        atomic_json(build / "artifacts.json", file_inventory(artifacts))
        atomic_json(build / "ready.json", {"identity": identity, "binaries": binaries})
    except BaseException as exc:
        # Do not change a sealed common archive, even if readiness publication failed.
        if not (build / "artifacts.json").exists():
            record.update(
                {
                    "status": "cancelled" if isinstance(exc, WorkerCancelled) else "failed",
                    "finished": now(),
                    "reason": str(exc),
                }
            )
            atomic_json(artifacts / "build.json", record)
            atomic_json(build / "artifacts.json", file_inventory(artifacts))
        raise
    return build, verify_build(build)


def prepare_run(source, build, directory, manifest):
    cwd = directory / "work"
    shutil.copytree(source, cwd, ignore=shutil.ignore_patterns("pgms", "tmp", "results"))
    (cwd / "tmp").mkdir(mode=0o700)
    (directory / "artifacts" / manifest["execution"]["artifact_dir"]).mkdir(
        mode=0o700, parents=True, exist_ok=True
    )
    return cwd, runtime_environment(manifest, build, directory)


def stabilize(directory, manifest, state):
    state.pop("wait_finished", None)
    state.update(
        {
            "status": "stabilizing",
            "wait_started": now(),
            "ready_at": time.time() + manifest["settle_seconds"],
        }
    )
    atomic_json(directory / "state.json", state)
    # The monotonic deadline prevents a wall-clock adjustment shortening the wait.
    deadline = time.monotonic() + manifest["settle_seconds"]
    while time.monotonic() < deadline:
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    state["wait_finished"] = now()


def run_warmup(directory, manifest, state, build, source):
    temporary = directory / "warmup"
    record = {"status": "preparing"}
    state["warmup"] = record
    try:
        cwd, env = prepare_run(source, build, temporary, manifest)
        verify_build(build)
        stabilize(directory, manifest, state)
        record.update(
            {
                "status": "warming_up",
                "started": now(),
                "wait_started": state["wait_started"],
                "wait_finished": state["wait_finished"],
                "ready_at": state["ready_at"],
                "command": {},
            }
        )
        verify_build(build)
        state["status"] = "warming_up"
        state.pop("ready_at", None)
        atomic_json(directory / "state.json", state)
        execution = manifest["execution"]
        run_process(
            execution["run_argv"],
            cwd,
            env,
            execution["timeout"],
            Path(os.devnull),
            Path(os.devnull),
            record["command"],
        )
        verify_build(build)
        record["status"] = "succeeded"
    except BaseException as exc:
        record.update(
            {
                "status": "cancelled" if isinstance(exc, WorkerCancelled) else "failed",
                "reason": str(exc),
            }
        )
        raise
    finally:
        record["finished"] = now()
        shutil.rmtree(temporary, ignore_errors=True)


def worker(manifest_path):
    os.umask(0o077)
    manifest_path = Path(manifest_path)
    directory = manifest_path.parent
    manifest = read_json(manifest_path)
    artifacts = directory / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    state = {"status": "preparing", "started": now(), "boot_id": boot_id(), "commands": {}}
    held_lock = None
    verified_build = None
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, cancellation_signal)
    try:
        held_lock = lock(directory.parent.parent / "worker.lock")
        if state["boot_id"] != manifest["boot_id"]:
            raise RuntimeError("boot ID changed before worker startup")
        atomic_json(directory / "state.json", state)
        build, source = prepare_build(directory, manifest, state)
        verified_build = build
        if manifest.get("warmup", False):
            run_warmup(directory, manifest, state, build, source)
        cwd, env = prepare_run(source, build, directory, manifest)
        execution = manifest["execution"]
        atomic_json(artifacts / "run-environment.json", env)
        verify_build(build)
        stabilize(directory, manifest, state)
        verify_build(build)
        state["status"] = "running"
        state["commands"]["run"] = {
            "argv": execution["run_argv"],
            "cwd": str(cwd),
            "started": now(),
            "timeout": execution["timeout"],
        }
        atomic_json(directory / "state.json", state)
        run_process(
            execution["run_argv"],
            cwd,
            env,
            execution["timeout"],
            artifacts / "run.stdout",
            artifacts / "run.stderr",
            state["commands"]["run"],
        )
        state["status"] = "succeeded"
    except WorkerCancelled as exc:
        state.update({"status": "cancelled", "reason": str(exc)})
    except Exception as exc:
        reason = str(exc)
        if state.get("warmup", {}).get("status") == "failed":
            reason = "warmup failed: " + reason
        state.update({"status": "failed", "reason": reason})
    finally:
        # Let systemd enforce its stop deadline rather than interrupting a manifest write.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if verified_build is not None:
            try:
                verify_build(verified_build)
            except Exception as exc:
                state["binary_error"] = str(exc)
                if state["status"] == "succeeded":
                    state.update({"status": "failed", "reason": str(exc)})
        state["finished"] = now()
        finalize(directory, state)
        if held_lock:
            held_lock.close()
    return 0 if state["status"] == "succeeded" else 1


def main():
    os.umask(0o077)
    action = sys.argv[1]
    if action == "worker":
        return worker(sys.argv[2])
    if action == "check-build":
        if sys.argv[3:] not in (["check"], ["all"]):
            raise RuntimeError("building during a benchmark is disabled")
        verify_build(Path(sys.argv[2]))
        return 0
    payload = json.loads(sys.argv[2])
    actions = {
        "probe": probe,
        "stage": stage,
        "start": start,
        "status": status,
        "ack": acknowledge,
        "cancel": cancel,
        "switch": switch,
    }
    try:
        value = actions[action](payload)
        print(json.dumps({"ok": True, "value": value}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
