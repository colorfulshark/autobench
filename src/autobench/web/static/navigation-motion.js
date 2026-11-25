let active;

export function clearNavigationMotion() {
  if (!active) return;
  const { target, animation } = active;
  active = undefined;
  delete target.dataset.navigationMotion;
  animation.cancel();
}

export function playNavigationMotion(target, motion = "page") {
  const restart = active?.target === target;
  clearNavigationMotion();
  if (
    !target?.getAnimations ||
    document.hidden ||
    matchMedia("(prefers-reduced-motion: reduce)").matches
  ) return;

  // Resolve the removed animation rule before repeating a transition on the same outlet.
  if (restart) target.getAnimations();
  target.dataset.navigationMotion = motion;
  const animation = target.getAnimations().find(
    (candidate) => candidate.animationName?.startsWith("ui-navigation-"),
  );
  if (!animation) {
    delete target.dataset.navigationMotion;
    return;
  }
  const current = { target, animation };
  active = current;
  const cleanup = () => {
    if (active !== current) return;
    active = undefined;
    delete target.dataset.navigationMotion;
  };
  animation.finished.then(cleanup, cleanup);
}
