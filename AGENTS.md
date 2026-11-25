# AGENTS.md

**AutoBench Web UI Design & Engineering Standards · v3.2**

> 本文件是完整的项目级 Web UI 开发约束；适用于 Coding Agent 与人工开发。具体技术栈、API、目录及测试命令以实际仓库为准。

## 1. Scope

本文件定义 AutoBench Web UI 的完整设计系统与前端工程规范，包括字体排版、控件、布局、响应式、图标、克制的渐变与动效、页面切换动画、交互流程、运行状态、数据可视化、文案、无障碍、安全、性能和测试，适用于所有现有及新增前端页面、共享组件和样式代码。

修改 UI 时必须遵循本规范。保持现有业务逻辑、配置语义、数据格式及交互行为不变；不要为了视觉统一而删除功能、改变默认值或引入不必要的依赖。

规范参考 Ant Design 的中后台排版体系、Material Design 3 的语义化字体层级以及 WCAG 2.2 AA 的无障碍要求。下文具体字号、颜色、布局尺寸和动效参数为 AutoBench 项目约定，不应误认为 WCAG 强制规定。

## 2. Core rules

**阅读导航：** 3–10 字体与原有验收；11–15 控件、渐变与动效（第 13 节，页面切换见 13.6）、CSS；16–19 布局、图标、流程及页面状态；20–24 任务状态、数据、文案与无障碍；25–30 工程、安全、性能、测试及治理；31 参考标准。涉及 UI 的任务必须阅读相关章节，不能只执行视觉部分。

- MUST：保持后端契约、配置默认值、权限及测试调度语义；所有状态、结果和进度必须来自真实数据。
- MUST：使用共享组件、统一语义 token 和页面布局原语；无障碍、安全、性能与相关回归测试是 UI 改造的一部分。
- MUST NOT：在视觉改造中引入未授权的真实服务器操作、伪造数据、泄露凭据或新增未要求的功能。

- MUST：使用全局 Design Tokens 和共享组件定义字体样式，禁止在业务组件中随意硬编码字号、行高、字重及文字颜色。
- MUST：默认正文、表单输入、按钮、下拉选项、复选框标签和表格正文统一使用 14px；页面标题使用 20px，一级区块标题使用 16px。
- MUST：采用系统 UI 无衬线字体栈，代码、命令、日志等需要字符对齐的内容使用等宽字体。
- MUST：保持清晰的视觉层级、足够的文字对比度，以及浏览器缩放与键盘操作的可用性。
- MUST NOT：使用 10px、11px 等过小字号，或通过缩小字号解决布局空间不足的问题。
- SHOULD：优先使用 400、500、600 三种字重；700 仅用于品牌或少量重要数据。
- SHOULD：保持现代、克制、专业的开发者工具风格；通过间距、布局和语义层级建立信息秩序，而不是普遍放大或加粗文字。
- MUST：渐变与动效遵循第 13 节的白名单及预算；使用公共 token 和组件，保留无动效、纯色及高对比度回退。允许适度视觉活力，但不得牺牲操作速度、信息密度、状态真实性或可访问性。
- MUST：真实页面导航、向导步骤和内容 Tab 切换采用第 13.6 节统一的轻量过渡；持久化 AppShell 保持稳定。禁止普通渲染、轮询和日志更新触发页面入场动画。减少动画模式和功能性回退优先。

## 3. Font families

### 3.1 UI font

使用系统原生字体，兼顾 Windows、macOS、Linux 和中英文混排，不额外引入在线字体依赖。

| 平台 | 优先字体 |
| --- | --- |
| Windows | Segoe UI、Microsoft YaHei |
| macOS | 系统字体（SF Pro）、PingFang SC |
| Linux | Noto Sans、Noto Sans CJK SC |
| Fallback | Arial、sans-serif |

### 3.2 Monospace font

使用等宽字体的场景：代码、Shell 命令、日志正文、原始运行事件、需要严格字符对齐的技术数据。

普通表单中的 IP、端口、路径、系统标识和 Benchmark 名称默认仍使用 UI 字体；仅在技术展示或字符对齐确有必要时切换为等宽字体。

统计数值、分数、时间等需要纵向对齐的数据优先使用 `font-variant-numeric: tabular-nums`，不必因此切换整个表格的字体。

## 4. Typography scale

所有 px 数值均以浏览器默认根字号 16px 为基准；实现时使用 rem 或等效的全局 token，并保留用户修改浏览器默认字体大小的能力。

| Token | Size | Line height | Weight | Usage |
| --- | ---: | ---: | --- | --- |
| `xs` | 12px | 18px | 400/500 | 非关键元数据、状态徽标 |
| `sm` | 13px | 20px | 400 | 辅助说明、路径提示、次要信息 |
| `base` | 14px | 22px | 400 | 正文、表单、表格、下拉选项 |
| `md` | 16px | 24px | 500/600 | 一级区块标题、重要信息 |
| `lg` | 18px | 26px | 600 | 较重要的模块标题 |
| `xl` | 20px | 28px | 600 | 页面标题 |
| `2xl` | 24px | 32px | 600/700 | 特殊一级标题、重点展示 |
| `3xl` | 32px | 40px | 600/700 | 统计指标、测试任务总数 |

优先使用 12、13、14、16、20、24、32px；18px 保留给确有需要的中间标题层级。不要为单个组件增加新的字号刻度。

## 5. Component typography

以下组件级规则优先于通用字号表。相同语义的组件在所有页面中应保持一致。

### 5.1 Navigation and headings

| Component | Size / Line height | Weight |
| --- | --- | ---: |
| AutoBench 品牌文字 | 18 / 26px | 700 |
| 顶部导航 | 14 / 20px | 500 |
| 侧栏步骤 | 14 / 22px | 400 |
| 当前步骤 | 14 / 22px | 600 |
| 页面标题 | 20 / 28px | 600 |
| 区块标题 | 16 / 24px | 600 |
| 次级分组标题 | 14 / 22px | 600 |

### 5.2 Forms and controls

| Component | Size / Line height | Weight |
| --- | --- | ---: |
| 字段标签 | 14 / 20px | 500 |
| 输入框文字、Placeholder | 14 / 22px | 400 |
| 下拉选项 | 14 / 22px | 400 |
| 下拉选中项 | 14 / 22px | 500 |
| 按钮文字 | 14 / 20px | 500 |
| Checkbox 标签 | 14 / 22px | 400 |
| 辅助说明 | 13 / 20px | 400 |
| 表单错误提示 | 13 / 20px | 400/500 |

### 5.3 Tables, status and technical data

| Component | Size / Line height | Weight |
| --- | --- | ---: |
| 表头 | 13 / 20px | 600 |
| 表格正文 | 14 / 22px | 400 |
| 系统名称 | 14 / 22px | 600 |
| 文件名称 | 14 / 22px | 500 |
| 次要文件路径 | 13 / 20px | 400 |
| 状态徽标、Boot 标识 | 12 / 18px | 500 |
| 统计数字 | 32 / 40px | 600 |
| 统计单位 | 13 / 20px | 400 |
| 时间、版本及次要元数据 | 13 / 20px | 400 |
| 表格中的主要时间字段 | 14 / 22px | 400 |
| 代码、日志与技术文本 | 13 / 20px | 400 |

## 6. Text colors and contrast

| Token | Value | Usage |
| --- | --- | --- |
| `--text-primary` | `#15253F` | 标题、正文、主要数值 |
| `--text-secondary` | `#475569` | 辅助说明、次要内容 |
| `--text-muted` | `#64748B` | 占位符、次要元数据 |
| `--text-accent` | `#2563EB` | 链接、可交互文字 |

- 普通文字与实际背景色的对比度必须达到 4.5:1；符合 WCAG 大号文字定义的内容至少达到 3:1。
- 在浅蓝背景、选中背景、表格行、弹窗等不同场景中均需验证对比度；必要时使用更深的语义颜色，而非盲目复用色值。
- 不要通过降低文字透明度或使用过浅灰色来制造辅助信息层级。
- 不应仅凭颜色传达错误、成功、选中等关键状态。

## 7. Layout and responsive behavior

以下尺寸为正常缩放下的推荐值，而非限制内容扩展的固定高度。

| Item | Recommended value |
| --- | --- |
| 标签与输入框间距 | 6–8px |
| 输入框高度 | 40px（正常内容基准，可增高） |
| 标准按钮高度 | 40px |
| 紧凑表格行高 | 44–48px |
| 普通表格行高 | 48–52px |
| 区块标题与内容间距 | 12–16px |
| 分区间距 | 24px |
| 卡片内部 padding | 20–24px |

- 主要布局间距遵循 4px 节奏；必要的 6px 等细粒度间距按组件规范使用。
- 优先使用 `min-height`、padding 和弹性布局，避免固定高度导致中文、长文本或缩放后的内容被裁剪。
- 1280px、1440px、1920px 桌面视口保持相同字号体系，通过布局、列数和留白适配视口；不要使用随视口宽度整体缩放字号的方案。
- 窄屏或高倍率缩放时，多列表单应能够重排为单列；长路径、GRUB Entry、运行 ID 等允许合理换行或提供完整内容查看方式。
- 不要使用缩小字号、隐藏关键字段或截断主要操作文字来换取空间。
- 中英文混排不依赖在中文字符之间插入空格来调整间距。

## 8. Reference implementation

先检查项目已有的全局样式、主题配置和共享组件，将以下语义映射到现有设计体系中。若已存在等效 tokens，优先复用或统一调整，避免创建并行的样式系统。以下 CSS 为参考实现，不要求项目采用特定 CSS 框架或 class 命名。

```css
:root {
  --font-ui:
    -apple-system, BlinkMacSystemFont, "Segoe UI",
    "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC",
    "Noto Sans", Arial, sans-serif;
  --font-mono:
    "SFMono-Regular", Consolas, "Liberation Mono",
    "Noto Sans Mono", monospace;

  --text-xs: 0.75rem;
  --text-sm: 0.8125rem;
  --text-base: 0.875rem;
  --text-md: 1rem;
  --text-lg: 1.125rem;
  --text-xl: 1.25rem;
  --text-2xl: 1.5rem;
  --text-3xl: 2rem;

  --leading-xs: 1.125rem;
  --leading-sm: 1.25rem;
  --leading-base: 1.375rem;
  --leading-md: 1.5rem;
  --leading-lg: 1.625rem;
  --leading-xl: 1.75rem;
  --leading-2xl: 2rem;
  --leading-3xl: 2.5rem;

  --weight-regular: 400;
  --weight-medium: 500;
  --weight-semibold: 600;
  --weight-bold: 700;

  --text-primary: #15253f;
  --text-secondary: #475569;
  --text-muted: #64748b;
  --text-accent: #2563eb;
}

html { font-size: 100%; }
body {
  font-family: var(--font-ui);
  font-size: var(--text-base);
  line-height: var(--leading-base);
  font-weight: var(--weight-regular);
  color: var(--text-primary);
}
button, input, select, textarea { font: inherit; }

.ui-page-title {
  font-size: var(--text-xl);
  line-height: var(--leading-xl);
  font-weight: var(--weight-semibold);
}
.ui-section-title {
  font-size: var(--text-md);
  line-height: var(--leading-md);
  font-weight: var(--weight-semibold);
}
.ui-field-label, .ui-button {
  font-size: var(--text-base);
  line-height: var(--leading-sm);
  font-weight: var(--weight-medium);
}
.ui-form-control, .ui-table {
  font-size: var(--text-base);
  line-height: var(--leading-base);
  font-weight: var(--weight-regular);
}
.ui-help-text {
  font-size: var(--text-sm);
  line-height: var(--leading-sm);
  color: var(--text-secondary);
}
.ui-code {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  line-height: var(--leading-sm);
}
.ui-number { font-variant-numeric: tabular-nums; }
.ui-stat-value {
  font-size: var(--text-3xl);
  line-height: var(--leading-3xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}
```

如果项目使用 Tailwind CSS 或其他组件库，应将上述 tokens 映射到已有主题配置，并在共享组件层统一字号和行高；不要在每个页面重复定义样式。

## 9. Page-specific requirements

| Page | Requirements |
| --- | --- |
| 连接机器 | 字段 14px，区块标题 16px，辅助说明 13px |
| 选择系统列表 | 系统名称 14px/600，Boot 标识 12px/500 |
| 系统详情 | 基本信息、配置选项、环境变量等区块标题 16px，输入及 GRUB Entry 选择框 14px |
| 设置测试（UnixBench） | 测试项名称 14px，次级分组标题 14px/600；通过分类、折叠、搜索或紧凑布局降低空间占用，不缩小文字 |
| 运行设置 | 参数名称与输入值 14px，说明 13px |
| 检查运行 | 统计数字 32px，表格正文 14px |
| 运行记录 | 表格正文及主要时间字段 14px，状态徽标 12px |
| 运行详情 | 文件树 14px，日志及代码内容使用等宽字体 |
| 弹窗及文件选择器 | 复用页面既有标题、表单、表格及按钮的语义字号，不单独发明字体体系 |

## 10. Agent workflow and acceptance criteria

修改前端时，Agent 应执行以下流程：

1. 检查适用的项目指令、现有主题 tokens、共享组件及目标页面，确定改动范围。
2. 优先在全局主题或共享组件层解决共性问题，页面级样式仅用于真实的布局差异。
3. 保留现有业务逻辑、配置项、默认值和交互语义；避免无关重构或新增依赖。
4. 运行项目已有的相关 lint、类型检查、测试或构建命令（以仓库实际工具链为准）。
5. 如具备浏览器验证能力，检查受影响页面及交互状态，至少覆盖正常桌面尺寸、窄屏和 200% 缩放场景；未能执行的检查必须明确说明，不得声称已经验证。

### Checklist

- [ ] 正文、输入框、下拉选项、主要按钮默认 14px，页面标题 20px，区块标题 16px。
- [ ] 字号、行高、字重和文字颜色复用统一 tokens；没有无理由的硬编码或临时覆盖。
- [ ] 没有 10px/11px 字号；12px 仅用于非关键元信息及状态徽标。
- [ ] 常规文字对比度至少 4.5:1，大号文字至少 3:1。
- [ ] 200% 文字缩放后不丢失内容或功能，并满足适用的 WCAG 2.2 AA 重排要求。
- [ ] 用户覆盖文字间距时内容仍然可读，不出现裁剪或重叠（WCAG 1.4.12：行高 1.5 倍、段后间距 2 倍、字距 0.12 倍、词距 0.16 倍字号）。
- [ ] 可点击目标满足 WCAG 2.2 AA 24×24 CSS px 最小目标要求或其允许的例外；常规按钮推荐至少 40px 高。
- [ ] 键盘焦点可见；不得为视觉美观移除焦点反馈而不提供替代样式。
- [ ] 数字列使用 `tabular-nums`；中英文、长路径及技术标识可读。
- [ ] 所有受影响页面及弹窗的字体层级一致，原有业务功能无回归。

## 11. Web UI component design system

### 11.1 Design principles and scope

本节与第 1–10 节共同构成 AutoBench Web UI 的开发约束。适用于连接机器、选择系统、系统详情、设置测试、运行设置、检查运行、运行记录、运行详情及所有弹窗、共享组件。视觉基准为已确定的浅蓝白色效果图，而非原始深色导航或浏览器默认控件样式。

- MUST：全局统一浅色主题、语义颜色、尺寸、圆角、图标、动效及状态；相同语义使用同一共享组件。优先复用现有技术栈和样式系统，不引入平行主题。
- MUST：保留配置语义、字段、默认值、校验、权限、任务调度及接口行为。只有用户明确要求时才改变业务交互；新增视觉状态不能虚构后端能力。
- MUST：优先使用原生语义元素；需要自定义 Select、Dialog 等时采用现有可访问组件或成熟 headless 实现，遵循对应 WAI-ARIA APG 模式。
- MUST：所有适用控件支持 default、hover、focus-visible、active、disabled；按语义支持 selected、checked、expanded、readonly、invalid、loading。状态不能只靠颜色表达。
- MUST NOT：使用厚重双层焦点框、浏览器默认样式与自定义样式混搭、无意义动画、随意硬编码颜色和尺寸、全局取消焦点轮廓却不提供替代。
- SHOULD：整体轻盈、克制、清晰；通过留白、对齐和信息分组提高密度，不缩小字体。渐变仅用于第 13.1 节白名单中的背景、顶栏、品牌图标、测试计划摘要和少量主操作；普通控件保持平面化。

### 11.2 Sizing, spacing and layout

以下为默认根字号 16px 时的 CSS px 基准，推荐用 rem tokens 实现；高度为 min-height 或布局基准而非不可扩展的固定高度。

| Component | Small | Default | Large |
| --- | --- | --- | --- |
| Input / Select / Number / Search | 32px | 40px | 48px |
| Button | 32px | 40px | 48px |
| Icon button | 32×32px | 40×40px | 48×48px |
| Checkbox / Radio 图形 | 16×16px | 18×18px | 20×20px |
| Switch 轨道 | 32×18px | 40×22px | 48×26px |
| Dropdown option | 32px | 36–40px | 44px |
| Table row | 44px | 48–52px | 56px |

- 默认使用 Default；Small 仅用于表格、工具栏、密集选项；Large 用于需要更大操作区域的场景。触屏优先提供 44px 目标。Checkbox、Radio、Switch 的图形尺寸不是点击目标尺寸；关联标签和可操作区域应满足 WCAG 2.5.8（24×24 CSS px 或允许的例外），项目优先使用 32px 及以上。
- 间距刻度 4、8、12、16、20、24、32、40、48px；6px 用于标签、菜单等细粒度间距。控件横向 padding 默认 12px，按钮 16px，图标和文字间距 8px，字段横向 gap 12–16px，标签与控件间距 6–8px，字段与提示间距 4–6px，卡片 padding 20–24px，分区 gap 24px。
- 同行的输入框、选择按钮、连接测试等必须等高且底部对齐。主机地址约 240–320px、端口 72–96px、用户名 120–160px、普通 Select 180–320px、轮数/超时等数字字段 96–160px（均为宽屏建议）；路径、SHA256、GRUB Entry 等长字段可弹性扩展。禁止将所有短字段无意义铺满整行。
- 使用 grid/flex、minmax(0,1fr)、min-width:0 等合理布局；窄屏或缩放时多列重排，避免固定宽度导致溢出。主内容和摘要可堆叠，弹窗不超出可视区域。

### 11.3 Colors and semantic states

沿用第 6 节文字 tokens。以下为 AutoBench 的项目色值，不是行业标准强制值。

| Token | Value | Usage |
| --- | --- | --- |
| `--ui-primary` | `#2563EB` | 主操作、链接、选中图标 |
| `--ui-primary-hover` | `#1D4ED8` | 主操作悬停 |
| `--ui-primary-active` | `#1E40AF` | 主操作按下 |
| `--ui-focus` | `#4D83DC` | 清晰的单层焦点边框 |
| `--ui-selected` | `#E8F1FF` | 选中项背景 |
| `--ui-hover` | `#F1F6FF` | 悬停背景 |
| `--ui-border-subtle` | `#C4D3E8` | 卡片、装饰边框、分隔线 |
| `--ui-control-border` | `#7689A6` | 必须识别的控件边界 |
| `--ui-surface` | `#FFFFFF` | 控件及面板背景 |
| `--ui-surface-alt` | `#F5F8FC` | 次级背景 |
| `--ui-page` | `#EAF1FC` | 页面背景 |
| `--ui-disabled-bg` | `#EDF1F6` | 禁用背景 |
| `--ui-disabled-fg` | `#64748B` | 禁用内容 |
| `--ui-overlay` | `rgba(21,37,63,.32)` | Modal 遮罩 |

| State | Foreground | Background |
| --- | --- | --- |
| Success | `#166534` | `#E8F5EC` |
| Warning | `#92400E` | `#FFF4DF` |
| Error | `#B42318` | `#FEECEB` |
| Info | `#1D4ED8` | `#E8F1FF` |
| Neutral / Cancelled | `#475569` | `#EDF1F6` |

- 文字对比度执行第 6 节；浅色背景上的 muted 文字若低于 4.5:1，改用 secondary；浅蓝选中背景上的链接按需改用 primary-hover。主按钮白字与主色背景需保持至少 4.5:1，若使用渐变须验证最浅处。
- 对识别控件或状态所必需的非文本视觉信息，按 WCAG 1.4.11 验证与相邻颜色至少 3:1（适用例外除外）。浅色装饰线不承担唯一的控件边界职责。
- 此处 `#4D83DC` 替代早期效果图中较淡的 `#7FA8ED` 作为焦点边框；保留细边框和 10% 光晕的设计，同时提高可见性。光晕是辅助，不是唯一焦点提示。
- 禁用态和只读态应区分：禁用不执行操作；只读允许查看、选择和复制值（适用时），锁定机器标签可显示锁图标，不显示编辑、删除或添加机器入口。

### 11.4 Radii, borders, shadows and icons

| Element | Radius |
| --- | --- |
| Checkbox | 4px |
| Status tag | 6px |
| Small control / Dropdown option | 8px |
| Default control | 10px |
| Large control / Dropdown panel | 12px |
| Card | 16px |
| Modal | 20px |
| Switch / Radio | 完全圆角 |

统一 1px 边框；焦点不改变控件实际尺寸。卡片阴影 `0 2px 8px rgba(31,57,99,.05)`，浮层 `0 8px 30px rgba(31,57,99,.10)`，Modal `0 16px 48px rgba(31,57,99,.16)`。普通输入框、按钮默认不添加投影。卡片背景与阴影保持轻量，避免卡片嵌套和无功能的留白。

图标统一使用现有线性图标库（如项目已经使用 Lucide，则继续复用），不混搭多个图标体系或用 emoji 代替操作图标。默认 16px，区块标题 20px，主要空状态 32–40px；线宽约 1.5–2px。图标按钮必须提供可访问名称，悬停提示仅补充说明，不是唯一标签。装饰性图标隐藏于辅助技术。危险操作在 hover/focus 时可用错误色，但不要把普通删除按钮永久做成大红实心按钮。

## 12. Component specifications

### 12.1 Input, Textarea, Number and Search

- 默认白色或极浅背景，1px 控件边框，10px 圆角，14px 文字。左侧图标与文本之间 8px；清除、密码显示、单位等尾部控件需保留空间，不覆盖文本。
- Hover 轻微加深边框；focus 使用单层 `--ui-focus` 边框与 3px 透明蓝光晕，键盘焦点必须可见。带图标的组合输入仅由外层 focus-within 绘制焦点效果，内部 input 不重复描边；不得同时绘制浏览器 outline 和自定义 ring。高对比度模式提供可见替代。
- Placeholder 不代替 label；必填项用标签、星号及程序化 required 状态表达。错误边框配合 13px 就地错误文本，使用 aria-invalid / aria-describedby 等正确关联。不得改变输入值或仅凭颜色报错。
- 密码默认遮挡，显示/隐藏按钮须有动态名称并保持焦点；日志、通知、错误输出不得泄露凭据。数字字段尊重现有最小/最大值、步长、单位和校验，不能擅自截断或重置。Textarea 可扩展，路径不做无意义格式转换。
- 搜索清空后恢复完整列表；对确需防抖的搜索采用 200–300ms，处理过期请求；不对普通表单输入添加延迟。中文输入法 composition 期间不提前提交搜索或触发表单确认。
- 只读、禁用、错误和加载状态避免布局跳动；复制和清除按钮行为符合业务语义。

### 12.2 Select, Dropdown, Combobox and Popover

- 触发框与 Input 同高；使用细线 ChevronDown，展开时旋转 180°，不使用粗黑原生三角。原生 select 如能满足需求可保留；如需完整定制，使用可访问组件，不能用无语义 div 模拟。
- 浮层白色或 `#FAFCFF`，1px 浅边框，12px 圆角，Level 2 阴影，内边距 4–6px，与触发器间距 4–6px；默认与触发器等宽，长 GRUB Entry 可适当加宽且不超出视口。最大高度建议 min(320px, 可用空间)，内容滚动。
- 选项 36–40px、14px/400、8px 圆角；hover 为 `--ui-hover`，selected 为 `--ui-selected` + 蓝色 Check + 500 字重；预留 Check 列宽避免文字左右跳动，键盘活动项与已选项可区分。长文本换行或提供完整可访问文本。禁用选项不得被激活。
- 根据可用空间自动翻转、偏移，必要时使用 portal，避免被容器裁剪；关闭后合理恢复焦点。键盘按对应 APG 模式实现（方向键、Enter/Space、Esc、Tab、必要时 Home/End 和 typeahead）；点击外部关闭，Esc 不应意外提交。Select 的选项列表与执行命令的 Menu 不混用角色。多选展示已选项数量或可删除标签，保留原有选值语义。
- 认证方式、密钥来源、代理、GRUB Entry、运行状态筛选复用同一组件。GRUB Entry 显示可读标题与实际标识，默认启动项和明确选择项应区分；无数据、探测中、失败、重试按现有功能展示，不能凭空生成启动项。

### 12.3 Buttons and links

| Variant | Default | Hover / Active |
| --- | --- | --- |
| Primary | 蓝底白字；关键主操作可使用第 13.1 节微渐变 | 主色加深，不缩放 |
| Secondary | 白/浅蓝底、细边框、蓝字 | 浅蓝背景、边框加深 |
| Ghost | 透明底、深蓝或强调色字 | 浅蓝背景 |
| Danger | 错误色文字/边框，确认主操作可用实心错误色 | 浅红或深红 |
| Link | 强调色文字，适用于导航 | 加深并提供明确交互反馈 |

- 同一局部任务只突出一个主操作，例如下一步、保存并检查、文件选择确认；连接测试、添加变量等辅助动作使用 Secondary 或 Ghost。取消/返回为 Secondary 或 Link。操作使用 button、导航使用 a；表单中的非提交按钮明确 `type="button"`。
- Loading 保持宽度和布局，显示 spinner 及任务语义，阻止重复提交；不把异步结果提前标记成功。禁用按钮不可触发动作，重要禁用原因应在附近说明。删除系统、取消运行等破坏性操作遵循既有确认及权限规则；需要确认时明确对象、影响及可取消入口。
- 列表排序首项禁用上移、末项禁用下移；操作后保持合理焦点。拖拽若存在必须提供按钮或其他单指针替代（WCAG 2.5.7）。仅选择系统步骤不允许新增、编辑或删除机器；连接机器步骤按既有逻辑提供这些动作。

### 12.4 Checkbox, Radio, Switch and selection groups

- Checkbox 用于独立多选；Radio 用于互斥选项；Switch 用于布尔开关，如预热。不得把开关变化理解为立即启动真实测试；保存/生效逻辑沿用业务定义。图形为 18px、Switch 40×22px（默认）；标签 14px，标签可点击并与控件关联。
- 未选为白底和清晰边框；已选为主色与白色 Check/圆点；中间态用短横线；Hover 轻微强调，Focus-visible 清晰，禁用不可触发。Switch 状态除颜色外应有滑块位置与程序化 checked 状态。Radio 组按方向键、Checkbox/Switch 按 Space 等对应原生或 APG 行为。
- UnixBench 测试项和测试组合应紧凑分类，优先多列选择、搜索、折叠或已选摘要；默认收起的区域有已选数量且不丢失选择；不能通过切换分组清空测试或更改默认 index 行为。全选/部分选中状态必须与实际数据一致，组合和单项的重叠语义保持既有行为。

### 12.5 Tabs, wizard navigation and accordion

- 顶栏统一浅蓝背景、深蓝文字、浅蓝选中态、蓝色下划线或现有选中标记，不在不同页面切换深色主题。左侧步骤导航顺序：连接机器、选择系统、设置测试、运行设置、检查运行；当前步骤突出蓝色，其他步骤中性，状态用数字/图标/文字明确。
- 步骤导航保持位置一致，不因内容变长移动；上一步/下一步保持业务校验，不允许通过纯视觉改造绕过校验。适当使用 aria-current="step"；路由导航不滥用 tab role。真实 Tab/Tabpanel 遵循 APG 键盘模式；页面、步骤与 Tab 内容切换统一执行第 13.6 节，不在组件内自建路由动效。
- Accordion 标题 14px/600、行高约 40px，Chevron 指明展开状态，按钮具有 aria-expanded。展开收起不销毁未提交值；有错误的折叠组提供可定位提示。高级设置仅用于确实低频的可选项，不能重复基础配置或隐藏必填错误。机器标签的锁定样式与可编辑样式需清楚区分。

### 12.6 Card, Table, status tag and data display

- 统一白色/浅色卡片、16px 圆角、浅边框、20–24px padding、细分隔线；信息区块标题 16px/600。右侧测试计划摘要保留统一蓝色头部、系统顺序和正式轮次/预热提示；机器名称是分组，不能误计为独立系统或任务。
- 表格使用浅灰蓝表头、13px/600，正文 14px，行高 48–52px；数值可右对齐并用 tabular-nums，文字左对齐。悬停浅蓝且不替代选中提示；操作列对齐、布局稳定。使用语义 table 和表头关联，只有真的可排序时才显示排序控件和 aria-sort。
- 状态标签 12px/500、6px 圆角、约 24px 高、水平 padding 8px；成功/失败/取消/运行中等使用一致语义和文字；不要给每个普通字段套彩色标签。进度条默认 6–8px 高，提供可访问名称和值；总数未知用 indeterminate 而非虚构百分比。运行中与结束状态必须来自真实任务数据。
- 文件树用 16px 图标、每级缩进 16–20px、行高至少 32px。文件名、大小、预览和下载对齐；长名称/路径提供完整值查看及复制。若使用 ARIA tree，实现其完整键盘模式，否则使用普通嵌套列表加 disclosure button。日志正文使用等宽字体，可滚动并支持选择复制，不自动滚动打断正在查看历史日志的用户。

### 12.7 Modal, server file picker and confirmations

- 弹窗标题 20px/600，20px 圆角，24px padding，Level 3 阴影，遮罩 `--ui-overlay`。建议小型确认宽 400–480px、标准 560px、文件选择器 720–800px，宽度不得超出 viewport 减 32px；最大高度约 85dvh，必要时内部滚动且页脚按钮可见。
- Header（标题/可选关闭）、Body、Footer 三部分；Footer 右对齐，取消在左、确认在右，按钮等高且间距 8–12px。遵循 Dialog APG：可访问标题、初始焦点、Tab/Shift+Tab 焦点约束、Esc 关闭（不可安全取消的流程需明确提示）、背景不可操作、关闭恢复焦点。遮罩点击是否关闭取决于既有业务及是否会丢弃输入。
- 文件选择器保留服务器端路径语义（不是浏览器本地上传），顶部上级目录按钮 + 路径框 + 前往按钮同高；隐藏文件开关、目录/文件列表、选中状态、底部取消/选择。目录双击/Enter 进入（如支持），文件单击选择；确认仅在合法目标已选时启用，文件与目录模式保持各自既有校验。空目录、权限失败、加载、过期请求等状态明确；不假装已访问或选中真实文件。
- 浮层层级按统一 token 管理：sticky/navigation < dropdown/popover < modal overlay < modal content < modal 内 popup/tooltip；不使用随意的 999999；浮层不遮挡键盘焦点或被滚动容器裁剪。

### 12.8 Feedback, loading, empty states and tooltips

- 表单错误优先就地显示；操作结果使用已有 Toast/Alert，不重复弹窗；信息/成功/警告/错误遵循语义色。重要错误保持可见直到处理，不仅依靠自动消失提示。非紧急提示用 status/live polite，紧急且确需打断的用 alert；不反复播报高频轮询。
- 异步加载优先使用局部 spinner 或骨架屏，保持控件和列表布局，不冻结无关区域；长任务显示真实进度或明确未知进度，取消/重试只在后端支持时提供。请求失败保留用户已输入配置，并提供可执行的下一步。
- 空列表说明为何为空（暂无运行、筛选无结果、目录为空），有真实可用操作时提供入口。Tooltip 13px，建议 300–500ms 后显示，支持 hover 和 focus、Esc 关闭，避免覆盖关键内容，不承载必须阅读或可交互的内容（交互内容使用 Popover）。

## 13. Restrained gradients, micro-interactions and motion

### 13.1 Visual language and gradient whitelist

设计目标：在原有浅蓝白色、紧凑的工程工具风格上增加轻微的空间感和交互反馈。采用 subtle gradients + purposeful micro-interactions；静态页面也必须完整、易读、可操作。渐变建立层级，动画解释状态变化；二者不能成为内容本身。

| 区域 | 默认视觉策略 | 推荐 token / 参数 |
| --- | --- | --- |
| AppShell 页面背景 | SHOULD：全站统一的极浅静态渐变，内容卡片仍为白色 | `--ui-gradient-page`：145deg，#F5F8FC → #EAF1FC |
| 顶部导航 | SHOULD：统一浅蓝渐变，保留深蓝文字和明确选中态 | `--ui-gradient-header`：110deg，#EAF1FC → #DCE8FA |
| 测试计划摘要 | SHOULD：仅头部使用蓝色微渐变，正文保持白色 | `--ui-gradient-summary`：110deg，#2563EB → #1D4ED8 |
| 关键主按钮 | MAY：下一步、开始测试等主要动作采用同色系微渐变 | `--ui-gradient-primary-overlay` 叠加在 `--ui-primary` 上，末端最多 10% 黑色遮罩；hover/active 仍使用既有主色 token |
| 品牌 | MAY：仅 Logo 图形或品牌小面积装饰使用主色渐变 | 复用 `--ui-gradient-summary`；品牌文字保持实色 |
| 步骤导航、Tab、机器标签、表单、表格、文件树、日志、弹窗内容 | MUST：平面背景与语义色，不增加渐变 | `--ui-surface`、`--ui-hover`、`--ui-selected` 等已有 token |
| 状态徽标、错误/警告、性能图表与进度填充 | MUST：使用明确实色与现有状态编码，不添加装饰渐变 | 保留第 11、20、21 节的语义 |

- 默认只使用同一蓝色系、两端色标、低反差线性渐变，不新建彩虹色、紫蓝撞色或高饱和装饰色。角度统一复用 token；不在页面组件中单独定义角度和色标。白底卡片不做渐变、玻璃拟态或大面积磨砂。
- 视觉预算：应用背景和顶栏属于全局底层，不计入强调区域；每个可见局部任务/视图最多两个蓝色渐变强调区域（例如计划摘要头部和下一步按钮），Logo 小图形除外。重复行、列表每项和所有主按钮不能同时使用渐变；若层级已足够则用实色。普通次级、危险、禁用按钮和只读/校验状态禁止装饰渐变。
- 文字对比度按第 6、11、24 节验证：渐变下的文字、图标及必要边界需要在整个实际使用区（含最浅处、hover/active、叠加层及各主题）满足适用阈值。若不满足，改用足够对比度的实色，不通过增加文字阴影掩盖问题。不得只用渐变表达选中、运行、成功或失败。
- 所有渐变必须保留独立的 `background-color` 纯色回退。forced-colors 下关闭装饰渐变，保留系统色、焦点、边界和选中标记。渐变不依赖图片、Canvas、JS 或外部资源；禁止动态背景渐变、呼吸光、流动光带、闪烁与无限背景动画。

### 13.2 Motion tokens and timing

| Motion | Duration | Recommended behavior |
| --- | --- | --- |
| Hover / Press color | 120ms | 颜色或背景轻微变化；不缩放按钮、不移动文本 |
| Focus border / ring | 160ms | 立即开始，细边框 + 柔和光晕；键盘焦点轮廓立即可见 |
| Chevron / Switch | 160ms | 箭头最多旋转 180°，滑块沿轨道移动 |
| Dropdown / Popover enter | 180ms | opacity + 不超过 4px 的局部位移 |
| Dropdown / Popover exit | 120ms | 淡出；交互和焦点按关闭语义立即处理 |
| Tabs / Step indicator | 160–180ms | 选中标记或背景切换；不滑动整个页面 |
| Accordion | 180–220ms（默认 200ms） | 局部展开收起，不销毁输入值 |
| Modal enter / exit | 200ms / 160ms | 淡入、最多 8px 位移；退出优先淡出 |
| Route / detail navigation | 180ms | 主内容 Fade Up，opacity + translateY(4px) |
| Wizard next / previous | 200ms | 主内容方向性淡入，translateX(+8px / -8px)，LTR |
| Tab panel switch | 150ms | 仅新内容淡入；选中标记仍按上表独立过渡 |
| File preview drawer enter / exit | 220ms / 160ms | 从右侧进入（LTR），关闭立即处理焦点和交互 |
| Background refresh / polling | 0ms | 不触发页面级入场或退出动效 |
| Toast enter / exit | 180ms / 120ms | 淡入、最多 4px 位移；不跳动布局 |
| Progress update | ≤ 200ms | 平滑跟随真实数据，仅适用于连续更新且不延迟终态 |
| Spinner cycle | 800–1000ms（默认 900ms） | 仅真实等待期间循环，结束立即停止 |

- 普通过渡使用 `--ui-ease`（`cubic-bezier(.2,0,0,1)`）；退出使用 `--ui-ease-exit`（`cubic-bezier(.4,0,1,1)`）；匀速 spinner 使用 linear。禁止弹跳、过冲、橡皮筋、夸张缩放、全页飞入。默认非循环交互动画不超过 220ms，不设置为了观赏而增加的延迟。
- 按需显式声明 transition 属性；优先 opacity、transform；小范围的 background-color、color、border-color、box-shadow 可用于控件状态。禁止 `transition: all`、对整个页面做 filter/blur 动画、持续动画化大面积阴影和渐变、因 hover 改变控件尺寸。Accordion 等必要布局变化限定在本地，避免逐帧 JS 测量引发布局抖动。

### 13.3 Micro-interaction patterns for AutoBench

| 场景 | SHOULD / MAY | MUST NOT |
| --- | --- | --- |
| 输入框、选择器 | 聚焦光晕 160ms；下拉面板 180ms 淡入与 4px 位移；Chevron 旋转 | 双层粗蓝框、点击时放大、焦点出现前延迟 |
| 按钮、图标按钮、系统行操作 | 120ms 颜色反馈；危险操作使用语义色；Loading 保持按钮尺寸 | 悬停上浮、弹跳、缩放、禁用时仍出现激活动效 |
| Tab、五步向导 | 选中背景/下划线轻量过渡；步骤切换 200ms 方向性淡入，Tab 内容 150ms 淡入，按 13.6 节执行 | 每次表单输入、轮询、刷新或切换筛选时重播入场；动画整套 AppShell |
| UnixBench 测试项和组合 | 折叠面板展开、Check/Switch 反馈；已选数量立即更新 | 每个选项依次飞入、动态计数滚动、折叠后清空选择 |
| 测试计划摘要 | 仅在用户编辑计划导致计数变化时可短暂淡入变化内容，默认直接更新 | 持续跳动数字、动画伪造任务数、整卡闪动 |
| 连接测试、自动识别、GRUB/EFI 读取 | 请求中显示真实 spinner + 文字；返回成功或失败时直接显示相应图标/文字，允许一次局部淡入 | 成功前预先打勾、持续发光、用动效替代错误说明 |
| 运行记录、运行详情 | 列表 hover 使用浅蓝；真实阶段变化时局部反馈；进度短过渡 | 轮询时整表重新入场、日志逐行动画、计数虚增、循环状态呼吸 |
| 文件选择器、Modal、Toast | 轻量浮层动效，目录切换直接更新，保留已有焦点管理 | 因动画延迟 Esc/取消、关闭后拦截点击、遮挡实际操作 |

- 主动、明确的用户交互优先于装饰动效。焦点、选中状态、表单值、ARIA 属性、验证、请求与真实进度应立即更新，不能等待 transitionend/animationend 才执行关键逻辑。退出动画元素不得继续接收操作或保留焦点；按组件实际实现正确处理 inert/移除、焦点恢复和浮层层级。
- 动效预算：单个操作默认只动画受影响控件和直接相关浮层；最多一个主要内容区入场效果，不做全页逐元素 stagger。静止页面无装饰性循环；仅真实的加载/不确定进度允许循环指示，终态、卸载或不可见时停止非必要动画。Toast/进度等不得因新轮询事件重复触发入场或累计动画队列。
- 快速连续切换必须可中断、可逆、不会产生陈旧状态、点击穿透或延迟响应；不要为播放动画引入计时器驱动的业务逻辑或新增动效库，优先共享组件的 CSS 或已存在的轻量动效能力。

### 13.4 Accessibility, performance and fallbacks

- MUST：支持 `prefers-reduced-motion: reduce`，非必要过渡和入场动画降为 0ms/禁用 keyframes，移除旋转和位移；保留 Switch 最终位置、Chevron 展开方向、键盘焦点、错误/选中提示以及真实加载文字。不能把整个界面禁用，也不能依赖动画结束事件完成业务操作。
- MUST：尊重 forced-colors 和高对比度环境；重要焦点与状态不依赖光晕、阴影或渐变。装饰层不得拦截鼠标、触摸或键盘；ARIA 状态和 live region 按第 24 节处理，不反复播报视觉变化。
- MUST NOT：自动播放装饰动画、闪烁、视差滚动、粒子、循环流光、动画背景、大面积模糊、hover 改变布局、仅凭动画表达信息；不为了动效改变滚动位置或自动滚动日志。对于真实不确定进度提供明确文字，不制造百分比。
- 优先使用 CSS transition、短 keyframes；不常驻 `will-change`，不为每个表格行强制创建合成层。不要动画化 gradient 色标/角度、全页 background-position、布局尺寸或大量元素的阴影；折叠面板仅在确需时局部改变尺寸，并验证大内容性能。
- 默认无需额外网络资源；不新增动效依赖。改造应在项目现有 Core Web Vitals 目标（第 28 节）下验证，不引入可见布局跳动、主线程长任务或高频轮询导致的重复动画。CI 截图应固定动效状态或使用 reduced-motion，交互测试另覆盖正常动效与快速切换。

### 13.5 Acceptance and change governance

- 新增渐变或动效必须属于上述白名单，复用已有 token，并有明确的信息层级或交互反馈目的；业务功能、配置含义和操作响应时间不变。超出预算或引入新颜色、时间尺度、动画库，需要说明理由并更新统一规范，不在单页私自扩展。
- 视觉验收同时检查连接机器、系统设置、设置测试、检查运行、运行记录和运行详情等受影响页面，保证统一浅蓝顶栏、白色主体、清晰的主次层级。既要检查静态截图，也要实际操作下拉、折叠、切换步骤和异步反馈；不以 AI 效果图代替浏览器验证。
- 最少覆盖默认/hover/focus/active/disabled、打开/关闭、快速重复操作、键盘、reduced-motion、forced-colors（支持时），以及真实加载结束、错误/取消、轮询刷新。报告实际执行结果和未验证项。

### 13.6 Page transitions and navigation motion

目标：点击导航或按钮切换页面时，提供短暂、自然、可预测的视觉连续性；静态布局及长期使用的专业感优先。页面过渡为共享导航能力，而不是页面私有装饰。除减少动画偏好或兼容性、性能等明确回退情况外，真实视图切换 MUST 使用以下默认策略。

#### 13.6.1 Scenario matrix

| Scenario | Duration | Motion / scope |
| --- | --- | --- |
| 顶部导航：测试配置 ↔ 运行记录 | 180ms | 新主内容 Fade Up，opacity 0 → 1，translateY(4px) → 0 |
| 系统列表 ↔ 系统详情、运行列表 ↔ 运行详情 | 180ms | 同一 Fade Up，不重复播放持久化外壳动效 |
| 五步向导：下一步 / 前进 | 200ms | opacity 0 → 1，translateX(+8px) → 0（LTR） |
| 五步向导：上一步 / 后退 | 200ms | opacity 0 → 1，translateX(-8px) → 0（LTR） |
| 报告 / 日志 / 事件等实际存在的内容 Tab | 150ms | 仅新 Tabpanel 淡入；无位移 |
| 文件预览 Drawer | 入场 220ms，退场 160ms | 从右侧滑入（LTR），遮罩按浮层语义淡入/淡出 |
| 文件选择器 Modal | 入场 200ms，退场 160ms | 淡入 + 向上移动最多 8px；退出优先淡出 |
| 高级设置等 Accordion | 默认 200ms | 仅本地内容展开/收起，保留输入 |
| 轮询、日志、进度、筛选、排序、分页、表单输入 | 0ms 页面动效 | 保持当前视图；允许第 13.2 节规定的局部状态反馈 |

- 入场统一复用 `--ui-ease`（`cubic-bezier(.2,0,0,1)`），退场复用 `--ui-ease-exit`。时长和位移只由第 14 节 token 管理。默认采用新内容单次入场，不强制先播放旧页面退场；可选的 crossfade 应在同一过渡时间预算内完成，不叠加等待时间。
- 向导方向由实际步骤索引或明确的导航意图决定，不按 URL 字符串排序或组件渲染次数猜测。RTL 环境按逻辑方向镜像（若项目支持）；普通跨模块路由默认 Fade Up，而非任意左右滑动。
- Drawer 是现有浮层的例外，可从实际停靠边缘完整滑入；页面及步骤主体位移不得超过 4px / 8px。不得为了应用动效新增不存在的 Tab、Drawer 或业务功能。

#### 13.6.2 Animation boundary and navigation lifecycle

- MUST：全局顶栏、AppShell 背景、仍存在的步骤导航与测试计划摘要保持稳定，不能整页重挂载或闪烁。只动画发生替换的 route outlet、step content 或 Tabpanel 内部内容；导航自身可更新选中背景、下划线与步骤标记。持久化侧栏不得随内容滑动。
- MUST：使用现有路由器/导航机制的已确认视图切换事件或稳定的视图身份（route/step/tab + 必要资源 ID）。同一视图重复点击、校验未通过、被取消的导航、普通组件 render、query/filter 更新、后端轮询和日志追加不得重播页面动画。不同运行 ID 等确实替换主视图的导航可以触发一次。首次直接打开或浏览器刷新默认不额外播放路由入场。
- MUST：动画仅用于视觉表现，不能等待 animationend、transitionend、任意 timeout 或 View Transition 完成才执行校验、路由提交、网络请求、表单状态保存、焦点恢复或可访问状态更新。异步数据按第 19、26 节加载，禁止以动画掩盖真实等待；骨架屏替换为数据时不重复播放页面入场。
- MUST：快速连续导航 A → B → C、浏览器前进/后退、Esc 关闭浮层等必须可中断并遵循最新有效导航。取消或替换旧动画，清理临时 class、监听器、快照和资源引用；不得出现陈旧页面重新显示、退出层遮挡点击、重复提交、丢失输入或遗留透明/位移状态。
- 保持原有路由历史、未保存更改保护、步骤校验、筛选和滚动恢复规则。新详情按现有导航语义定位，返回列表恢复滚动和合理焦点；Tab 切换不强制滚至页面顶端。文档标题、aria-current、aria-selected、焦点与页面真实语义立即同步。不要将焦点移入已退出或隐藏的 DOM，也不要因后台数据更新抢焦点。
- 动画容器选在不会破坏 sticky/fixed 定位、portal 浮层、滚动根及长表格的内容内层；避免长期设置 transform、overflow:hidden 或 will-change。新旧页面不得同时参与正常布局或产生双重可访问内容；旧快照仅用于装饰，不可交互。滚动条、卡片宽高和背景保持稳定，不因动画产生横向溢出或可见布局跳动。

#### 13.6.3 Implementation and progressive enhancement

- 默认使用第 14 节的共享 CSS keyframes 和语义 token，结合现有技术栈在导航确认后触发一次。优先共享 PageTransition/RouteOutlet/WizardLayout/Tabpanel 或等效机制，保持现有框架生命周期；不要创建平行路由器或为每个页面复制动画。禁止为了简单过渡新增动画库。
- MAY：现有路由框架和目标浏览器支持时，使用 `document.startViewTransition()` 改善新旧内容的视觉连续性。必须特性检测并遵循减少动画偏好；仅在实际 DOM 提交可协调且不会影响交互、焦点和敏感内容时使用。限制快照和动画在实际变化的内容区域，避免默认 root crossfade 让顶栏或背景一起闪动；为命名的过渡元素使用唯一 `view-transition-name`，不要捕获密码等敏感区域。
- View Transition API 不可用、过渡被跳过或捕获失败时，保留同一导航结果并安全回退至共享 CSS 入场或直接切换；禁止重复执行有副作用的导航/提交回调。若框架提供原生路由过渡集成则优先复用。API 路径与 CSS 入场互斥，不叠加两套动画；不得等待过渡结束才允许下一次导航。必要时跳过旧的活跃过渡（如受支持的 `skipTransition()`），业务导航仍按正常流程处理。
- MUST：`prefers-reduced-motion: reduce` 下页面、步骤、Tab、Drawer 和 Modal 的非必要入场/退出及位移全部取消；仍保留导航反馈、真实加载提示、焦点及完整功能。forced-colors 模式保留清晰边界与选中态。浏览器不支持动画时直接切换必须完全可用。

#### 13.6.4 Navigation motion acceptance

- [ ] 顶部两个模块、五步向导前进/后退、列表与详情、实际存在的报告/日志 Tab、文件预览和文件选择器均按场景规则验证；持久化 AppShell 无重复动画。
- [ ] 快速 A → B → C、连续点击、浏览器前进/后退、校验阻止跳转、未保存配置取消导航及异步加载，不出现陈旧页面、重复动效或操作延迟。
- [ ] 轮询、日志刷新、进度、表格筛选/分页、普通渲染均不触发页面动效；原有表单、选择、焦点和滚动恢复行为保持正确。
- [ ] 默认 CSS、减少动画模式和不支持 View Transition API 的回退路径功能等价；采用可选 API 时另测跳过/中断/失败，验证不会双重执行导航或叠加动画。
- [ ] 在代表性桌面、窄屏、高倍率缩放及长报告/大日志数据下验证无布局抖动、横向溢出、遮挡焦点或明显性能回退；静态视觉截图固定动效状态，正常动效另做行为测试。

## 14. Component Design Tokens and reference CSS

与第 8 节合并到同一个主题文件。项目如果已经存在等效变量，应映射复用。以下代码说明视觉规则，不替代可访问组件的行为实现。

```css
:root {
  --ui-primary: #2563eb;
  --ui-primary-hover: #1d4ed8;
  --ui-primary-active: #1e40af;
  --ui-focus: #4d83dc;
  --ui-selected: #e8f1ff;
  --ui-hover: #f1f6ff;
  --ui-border-subtle: #c4d3e8;
  --ui-control-border: #7689a6;
  --ui-surface: #fff;
  --ui-surface-alt: #f5f8fc;
  --ui-page: #eaf1fc;
  --ui-header: #dce8fa;
  --ui-gradient-page: linear-gradient(145deg, var(--ui-surface-alt), var(--ui-page));
  --ui-gradient-header: linear-gradient(110deg, var(--ui-page), var(--ui-header));
  --ui-gradient-summary: linear-gradient(110deg, var(--ui-primary), var(--ui-primary-hover));
  --ui-gradient-primary-overlay: linear-gradient(110deg, transparent, rgba(0,0,0,.10));
  --ui-disabled-bg: #edf1f6;
  --ui-disabled-fg: #64748b;
  --ui-overlay: rgba(21,37,63,.32);
  --ui-success: #166534;
  --ui-success-bg: #e8f5ec;
  --ui-warning: #92400e;
  --ui-warning-bg: #fff4df;
  --ui-error: #b42318;
  --ui-error-bg: #feeceb;
  --ui-info: #1d4ed8;
  --ui-info-bg: #e8f1ff;
  --ui-neutral: #475569;
  --ui-neutral-bg: #edf1f6;
  --ui-space-1: .25rem;
  --ui-space-2: .5rem;
  --ui-space-3: .75rem;
  --ui-space-4: 1rem;
  --ui-space-5: 1.25rem;
  --ui-space-6: 1.5rem;
  --ui-space-8: 2rem;
  --ui-space-10: 2.5rem;
  --ui-space-12: 3rem;
  --ui-height-sm: 2rem;
  --ui-height-md: 2.5rem;
  --ui-height-lg: 3rem;
  --ui-radius-xs: .25rem;
  --ui-radius-tag: .375rem;
  --ui-radius-sm: .5rem;
  --ui-radius-md: .625rem;
  --ui-radius-lg: .75rem;
  --ui-radius-card: 1rem;
  --ui-radius-modal: 1.25rem;
  --ui-border-width: 1px;
  --ui-icon-sm: 1rem;
  --ui-icon-md: 1.25rem;
  --ui-shadow-card: 0 2px 8px rgba(31,57,99,.05);
  --ui-shadow-popup: 0 8px 30px rgba(31,57,99,.10);
  --ui-shadow-modal: 0 16px 48px rgba(31,57,99,.16);
  --ui-focus-ring: 0 0 0 3px rgba(59,130,246,.10);
  --ui-motion-fast: 120ms;
  --ui-motion-focus: 160ms;
  --ui-motion-popup: 180ms;
  --ui-motion-modal: 200ms;
  --ui-motion-accordion: var(--ui-motion-modal);
  --ui-motion-exit: var(--ui-motion-fast);
  --ui-motion-spinner: 900ms;
  /* Navigation motion: reuse existing timing/offset primitives. */
  --ui-motion-page: var(--ui-motion-popup); /* 180ms */
  --ui-motion-step: var(--ui-motion-modal); /* 200ms */
  --ui-motion-tab: 150ms;
  --ui-motion-drawer: 220ms;
  --ui-motion-drawer-exit: var(--ui-motion-focus); /* 160ms */
  --ui-motion-modal-exit: var(--ui-motion-focus); /* 160ms */
  --ui-motion-offset-sm: 4px;
  --ui-motion-offset-md: 8px;
  --ui-ease-exit: cubic-bezier(.4,0,1,1);
  --ui-ease: cubic-bezier(.2,0,0,1);
  --ui-z-sticky: 10;
  --ui-z-popup: 100;
  --ui-z-overlay: 200;
  --ui-z-modal: 210;
  --ui-z-modal-popup: 220;
  --ui-z-toast: 230;
}

/* Static gradient surfaces: map these aliases to existing shared components. */
.ui-app-shell {
  background-color: var(--ui-page);
  background-image: var(--ui-gradient-page);
}
.ui-app-header {
  background-color: var(--ui-header);
  background-image: var(--ui-gradient-header);
  color: var(--text-primary);
}
.ui-plan-header {
  background-color: var(--ui-primary);
  background-image: var(--ui-gradient-summary);
  color: #fff;
}

/* Standalone control OR a composite wrapper, never both. */
.ui-control {
  box-sizing: border-box;
  min-height: var(--ui-height-md);
  padding: .4375rem var(--ui-space-3); /* 22px line + 14px padding + 2px border; min-height 40px */
  border: var(--ui-border-width) solid var(--ui-control-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-surface);
  color: var(--text-primary);
  font-family: var(--font-ui);
  font-size: var(--text-base);
  line-height: var(--leading-base);
  transition: border-color var(--ui-motion-focus) var(--ui-ease),
              box-shadow var(--ui-motion-focus) var(--ui-ease),
              background-color var(--ui-motion-fast) var(--ui-ease);
}
.ui-control:hover:not(:disabled):not([aria-disabled="true"]) {
  border-color: var(--ui-focus);
}
.ui-control:focus,
.ui-control--composite:focus-within {
  border-color: var(--ui-focus);
  box-shadow: var(--ui-focus-ring);
  outline: none; /* Local replacement, never a global reset. */
}
/* Keyboard focus: a single stronger boundary; no extra nested outline. */
.ui-control:focus-visible,
.ui-control--composite:has(> input:focus-visible) {
  border-color: var(--ui-primary-active);
}
.ui-control--composite > input {
  min-width: 0;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  font: inherit;
}
.ui-control--composite > input:focus {
  outline: none; /* Wrapper provides the indicator. */
}
.ui-control[aria-invalid="true"],
.ui-control--composite[data-invalid="true"] {
  border-color: var(--ui-error);
}
.ui-control[aria-invalid="true"]:focus,
.ui-control--composite[data-invalid="true"]:focus-within {
  box-shadow: 0 0 0 3px rgba(180,35,24,.12);
}
.ui-control:disabled, .ui-control[aria-disabled="true"] {
  background: var(--ui-disabled-bg);
  color: var(--ui-disabled-fg);
  border-color: var(--ui-border-subtle);
  cursor: not-allowed;
}
.ui-button-primary {
  min-height: var(--ui-height-md);
  padding: .5rem var(--ui-space-4);
  border: 1px solid transparent;
  border-radius: var(--ui-radius-md);
  background-color: var(--ui-primary);
  color: white;
  font-size: var(--text-base);
  line-height: var(--leading-sm);
  font-weight: var(--weight-medium);
  transition: background-color var(--ui-motion-fast) var(--ui-ease);
}
.ui-button-primary:hover:not(:disabled):not([aria-disabled="true"]) {
  background-color: var(--ui-primary-hover);
}
.ui-button-primary:active:not(:disabled):not([aria-disabled="true"]) {
  background-color: var(--ui-primary-active);
}
.ui-button-primary:disabled, .ui-button-primary[aria-disabled="true"] {
  background: var(--ui-disabled-bg);
  color: var(--ui-disabled-fg);
  cursor: not-allowed;
}
/* Explicit opt-in: only key primary actions, within the gradient budget.
   Static overlay; hover/active animate the underlying solid color. */
.ui-button-primary--gradient:not(:disabled):not([aria-disabled="true"]) {
  background-image: var(--ui-gradient-primary-overlay);
}
.ui-button-primary:focus-visible, .ui-icon-button:focus-visible {
  outline: 2px solid var(--ui-focus);
  outline-offset: 2px;
}
.ui-dropdown-panel {
  padding: .375rem;
  border: 1px solid var(--ui-border-subtle);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-surface);
  box-shadow: var(--ui-shadow-popup);
  max-height: 20rem; /* Limit further to available viewport space. */
  overflow: auto;
}
.ui-option {
  min-height: 2.25rem;
  padding: .375rem var(--ui-space-2);
  border-radius: var(--ui-radius-sm);
}
.ui-option:hover, .ui-option[data-highlighted="true"] {
  background: var(--ui-hover);
}
.ui-option[aria-selected="true"] {
  background: var(--ui-selected);
  color: var(--ui-primary-hover);
  font-weight: var(--weight-medium);
}
.ui-option[data-highlighted="true"] {
  outline: 1px solid var(--ui-focus);
  outline-offset: -1px;
}
/* Apply only to an explicit enter event in the existing component lifecycle.
   Do not remount content or toggle this attribute on polling/render updates. */
@keyframes ui-enter-soft {
  from { opacity: 0; transform: translateY(var(--ui-motion-offset-sm)); }
  to { opacity: 1; transform: none; }
}
.ui-motion-enter[data-entering="true"] {
  animation: ui-enter-soft var(--ui-motion-popup) var(--ui-ease) both;
}
/* Page/step/tab motion: set only after a real committed navigation.
   Apply to a shared content inner wrapper, not the AppShell or sticky root.
   Remove the transient attribute/class on completion or interruption;
   this cleanup is visual only, never a prerequisite for navigation. */
@keyframes ui-step-enter {
  from { opacity: 0; transform: translateX(var(--ui-step-enter-x)); }
  to { opacity: 1; transform: none; }
}
@keyframes ui-tab-enter {
  from { opacity: 0; }
  to { opacity: 1; }
}
.ui-page-content[data-navigation-motion="page"] {
  animation: ui-enter-soft var(--ui-motion-page) var(--ui-ease) both;
}
.ui-page-content[data-navigation-motion="forward"] {
  --ui-step-enter-x: var(--ui-motion-offset-md);
}
.ui-page-content[data-navigation-motion="backward"] {
  --ui-step-enter-x: calc(-1 * var(--ui-motion-offset-md));
}
.ui-page-content[data-navigation-motion="forward"],
.ui-page-content[data-navigation-motion="backward"] {
  animation: ui-step-enter var(--ui-motion-step) var(--ui-ease) both;
}
.ui-page-content[data-navigation-motion="tab"] {
  animation: ui-tab-enter var(--ui-motion-tab) var(--ui-ease) both;
}
/* Optional RTL direction support (if the application supports RTL). */
[dir="rtl"] .ui-page-content[data-navigation-motion="forward"] {
  --ui-step-enter-x: calc(-1 * var(--ui-motion-offset-md));
}
[dir="rtl"] .ui-page-content[data-navigation-motion="backward"] {
  --ui-step-enter-x: var(--ui-motion-offset-md);
}
/* Drawer: use the existing overlay lifecycle, not a route animation. */
@keyframes ui-drawer-enter {
  from { transform: translateX(100%); }
  to { transform: none; }
}
@keyframes ui-drawer-exit {
  from { transform: none; }
  to { transform: translateX(100%); }
}
.ui-file-drawer[data-motion="enter"] {
  animation: ui-drawer-enter var(--ui-motion-drawer) var(--ui-ease) both;
}
.ui-file-drawer[data-motion="exit"] {
  animation: ui-drawer-exit var(--ui-motion-drawer-exit) var(--ui-ease-exit) both;
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --ui-motion-fast: 0ms;
    --ui-motion-focus: 0ms;
    --ui-motion-popup: 0ms;
    --ui-motion-modal: 0ms;
    --ui-motion-tab: 0ms;
    --ui-motion-drawer: 0ms;
    /* Page, step and exit aliases resolve to zero via the base tokens. */
    --ui-motion-offset-sm: 0px;
    --ui-motion-offset-md: 0px;
  }
  .ui-motion-enter[data-entering="true"],
  .ui-page-content[data-navigation-motion],
  .ui-file-drawer[data-motion] {
    animation: none;
    opacity: 1;
    transform: none;
  }
  /* Also disable non-essential keyframes in existing shared components.
     Spinner uses a static indicator plus loading text in this mode. */
}
@media (forced-colors: active) {
  :root {
    --ui-gradient-page: none;
    --ui-gradient-header: none;
    --ui-gradient-summary: none;
    --ui-gradient-primary-overlay: none;
  }
  .ui-control:focus-visible,
  .ui-control--composite:focus-within,
  .ui-button-primary:focus-visible,
  .ui-icon-button:focus-visible {
    outline: 2px solid Highlight;
    outline-offset: 2px;
    box-shadow: none;
  }
}
```

注意：CSS 的 aria-disabled 选择器只提供视觉效果；自定义控件仍必须阻止禁用操作。Select 活动项的属性应映射所选组件库的真实状态。复合控件内独立按钮的键盘焦点仍需单独可辨识，且不能重复给整个输入框描边。原生控件保留可见的焦点反馈，除非有验证过的替代样式。

页面切换示例中的 `data-navigation-motion` 由现有路由/步骤/Tab 生命周期驱动，不是永久挂在页面上的样式。若采用可选 View Transition API，必须按 13.6.3 处理 DOM 提交、过渡范围与回退，并禁用该次 CSS 入场；Drawer 示例以右侧停靠、LTR 为基准，其他停靠方向按实际布局适配。退出元素在动效期间不可交互，减少动画或过渡中断时也要完成实际关闭及视觉清理。以上 CSS 不替代路由、焦点、历史或浮层生命周期实现。

## 15. Component acceptance criteria

本节补充第 10 节验收流程，Agent 必须按实际改动范围检查，不能因仅修改 CSS 就跳过交互回归。

- [ ] 全局浅色导航、测试计划卡、输入框、按钮、图标及弹窗视觉统一；选择系统步骤的只读机器标签没有编辑/删除/新增入口。
- [ ] 同行控件等高，默认 40px，短数字输入不铺满容器；表单缩放可重排且无文字裁剪。
- [ ] 所有共享控件使用 tokens，覆盖 default/hover/focus/active/disabled 及适用的 selected/error/loading/readonly；无双重蓝框、重复阴影、布局跳动。
- [ ] 常规文本至少 4.5:1；适用的控件边界、关键状态及焦点指示至少 3:1；颜色不是唯一状态线索；在实际背景、hover/selected 和高对比度场景验证。
- [ ] Tab/Shift+Tab 焦点顺序合理、可见且不被 sticky 或弹窗遮挡；Enter/Space/Esc、方向键及各控件对应的键盘行为符合语义；Modal 关闭正确恢复焦点。
- [ ] 输入框清除、密码显示、中文输入法、错误状态与原有值保存正常；Select 翻转、防裁剪、选中态、长内容、键盘操作及禁用选项正常。
- [ ] 测试项折叠/筛选不丢失选项；GRUB Entry、Boot 标识、机器/系统/轮次计数保持真实业务含义；文件选择器目录切换、隐藏文件、选择有效性与取消正常。
- [ ] 加载态防重复提交、错误保留配置、运行状态和进度来自实际数据；不泄露密码/密钥；原有权限、API 和配置格式不变。
- [ ] reduced-motion 下无非必要动画，forced-colors 下焦点与状态清晰；关键文字、触控区域满足第 10 节要求。
- [ ] 渐变仅在第 13 节白名单及视觉预算内使用；最浅背景处对比度合格，静态/纯色回退正常；动画可中断、无装饰性循环、无重复入场、无业务响应延迟。
- [ ] 页面、步骤、Tab 与文件预览切换通过第 13.6.4 节验收；共用动效 tokens、持久外壳稳定、快速导航及回退正常。
- [ ] 在 1280/1440/1920px、窄屏、200% 缩放检查布局；若目标为 WCAG AA，另验证适用的 320 CSS px 重排要求（确需二维布局的内容允许标准例外）。检查支持的 Chromium/Firefox/Safari（可用时），必要时记录 OS 差异。
- [ ] 执行已有 lint、类型检查、组件/端到端测试与构建；有浏览器测试能力时保存关键状态截图。报告实际执行结果、未验证项与原因，不声称未做过的测试已通过。

## 16. Application layout, grid and responsive design

### 16.1 App shell and spacing

- MUST：所有页面复用同一 AppShell，顶栏、品牌、导航选中态、配置文件入口、页面背景、全局浮层宿主一致。默认浅蓝白色主题，不混用深色顶栏；测试配置的五步流程共享 WizardLayout，运行记录/详情使用适合列表及详情的内容布局，不强制套三栏。
- 默认页面背景 `--ui-page` + `--ui-gradient-page`；顶栏统一 `--ui-gradient-header`，正文白色卡片、深蓝文字、蓝色选中态；右侧测试计划头部使用 `--ui-gradient-summary`。渐变、纯色回退、对比度及强调区域预算遵循第 13 节。
- 页面标题只出现一次；已约定不显示重复“连接服务器”“系统详情”等标题时，不额外添加视觉标题，但保留合理的程序化标题及导航语义。

| Layout token | Desktop default | Notes |
| --- | --- | --- |
| Header min-height | 64px | 允许缩放增高 |
| Step sidebar | 224px（216–240px） | 不随步骤内容跳动 |
| Plan sidebar | 300px（280–320px） | 摘要使用真实数据 |
| Page padding | 24px | 窄屏 16px，极窄 12px |
| Shell gap | 24px | 空间不足可降至 16–20px |
| Card padding | 24px | 紧凑 20px，窄屏 16px |
| Section gap | 24px | 标题至正文 12–16px |
| Form row gap | 16px | 错误提示允许增高 |
| Form column gap | 16px | 宽屏可用 24px |
| Field label gap | 6–8px | 提示与控件间距 4–6px |

- 主内容采用 `minmax(0,1fr)`，不固定页面总宽；普通表单可使用 12 列 CSS Grid 或等效的 flex/grid 布局。主机/用户名/端口等短字段按 11.2 节确定宽度，长路径、GRUB Entry 等按实际需要伸展；同一行标签与控件基线对齐。
- 使用统一页面滚动策略：优先单一主页面垂直滚动；导航和计划摘要可 sticky，考虑顶栏高度和焦点可见性；只有文件树、日志、数据表等有实际需要的区域才独立滚动。禁止页面横向溢出（确需二维布局的内容在局部容器内滚动）。滚动条尊重系统偏好；可定制时宽度约 8px，不隐藏键盘可操作滚动条。
- 每个页面或局部任务最多一个主要操作。Wizard 的上一步/下一步位置统一；提交按钮不能随错误出现而消失。固定操作区需留足底部滚动空间，不能遮挡字段或焦点。列表、弹窗、卡片之间使用可复用布局原语而不是嵌套装饰容器。

### 16.2 Breakpoints and content-driven adaptation

以下是 AutoBench 的布局断点（CSS viewport px），不是设备型号或行业强制断点；如果实际主内容不能合理容纳，提前切换布局。不得按视口缩放基础字体。

| Viewport | Shell | Form behavior |
| --- | --- | --- |
| ≥1600px | 三栏，侧栏在规定范围内 | 多列，避免短字段过宽 |
| 1280–1599px | 标准三栏；空间不足时摘要折叠 | 2–3 列按内容调整 |
| 1024–1279px | 左导航 + 内容；摘要折叠/下移 | 1–2 列 |
| 768–1023px | 紧凑步骤导航或可访问抽屉 | 优先单列 |
| <768px | 单栏，导航抽屉或步骤条，摘要可展开 | 单列，关键按钮可换行 |

- 在 320 CSS px 等效宽度下验证适用的 WCAG 重排；数据表、图表等必要二维内容使用局部可操作滚动，不能以此为由让整个表单横向滚动。
- 用户 200% 文本缩放、浏览器 400% 页面缩放/320 CSS px 测试分别验证，不混为同一要求。支持系统大字体、触屏及高 DPI；控件允许增高，截断值提供完整查看。抽屉/摘要折叠不丢失数据或阻断提交。
- 宽屏表单使用内容宽度上限而非无意义的满宽控件；弹窗使用 `max-inline-size` 和 `max-block-size`，避免固定高宽导致裁剪。页面跳转和锚点考虑 sticky 高度（如 `scroll-margin-block-start`）。页面切换动画仅作用于变化的内容内层，不改变共享 AppShell 和滚动根的身份，遵循第 13.6 节。

## 17. Iconography and visual asset standards

- MUST：复用项目现有线性图标库；若已有 Lucide，沿用其组件与语义映射，不另引入图标库。优先 SVG；不使用 emoji、图片截图、Unicode 箭头替代正式图标。线宽统一 1.5–2px，端点及转角风格一致；颜色默认继承 `currentColor`。
- 标准行内图标 16px，标题 20px，主要空状态 32–40px，紧凑图标可用 14px。图标尺寸不同于点击热区；图标按钮按 11.2 节及 WCAG 2.5.8 处理。独立图标按钮具备可访问名称、可见焦点和适当提示。

| Semantic role | Suggested Lucide name (map to existing equivalent) |
| --- | --- |
| 机器 / 系统 / 测试 | server / monitor / flask-conical |
| 设置 / 运行记录 | settings / history |
| 文件 / 目录 / 日志 | file-text / folder / terminal |
| 添加 / 编辑 / 删除 | plus / pencil / trash-2 |
| 刷新 / 自动识别 | refresh-cw / scan-search |
| 运行 / 停止 / 取消 | play / square / x |
| 下载 / 复制 | download / copy |
| 上移 / 下移 / 展开 | arrow-up / arrow-down / chevron-down |
| 成功 / 警告 / 失败 / 信息 | circle-check / triangle-alert / circle-x / info |
| 只读 / 密码显示 | lock / eye / eye-off |

同一操作跨页面使用同一图标及动词；状态图标不能代替状态文本。图标语义以实际动作而非形状为准（删除与关闭、停止与取消不可互换）。品牌资产使用项目已有文件，保留比例；设计渲染图仅作为参考，不将整张 UI 图当作背景或运行时控件。

## 18. Interaction patterns and configuration workflow

### 18.1 Wizard and draft semantics

- MUST：五个步骤顺序固定为连接机器 → 选择系统 → 设置测试 → 运行设置 → 检查运行。步骤状态区分未访问、当前、已完成、存在错误；是否允许跳步、返回或修改已完成步骤遵循现有校验与实际业务约束。
- 切换步骤、折叠组件、打开/关闭选择器、路由返回不得意外丢失输入。派生摘要从同一个配置模型计算；禁止复制成另一份手动维护的状态。已确认的检查计划与真正提交的配置须一致；上游修改使下游结果失效时，标记需要重新检查，不静默继续使用旧结果。
- 如果支持草稿恢复，采用版本化、非敏感数据持久化，并明确保存状态、来源与恢复策略；密码、私钥、令牌绝不持久化到浏览器存储。没有既有持久化能力时不得虚构保存成功。离开有真实未保存更改的页面才提示；不对每次点击弹出确认。
- 表单验证优先使用后端已有规则和可共享 schema。提交时展示全部可修复错误，聚焦首个无效字段、展开其所在区域并保留输入；输入中避免持续报错，采用合理的 blur/submit 校验。异步校验不覆盖更新后的值。
- 条件字段的显示与是否参与序列化分别遵循已有配置契约；隐藏不等于删除或禁用。认证方式、密码来源、代理、GRUB 等切换不应擅自改写其它字段。显示继承值与显式覆盖值，提示覆盖的实际作用范围。

### 18.2 AutoBench-specific patterns

| Page / operation | Required behavior |
| --- | --- |
| 连接机器 | 机器标签允许现有新增、编辑、删除；连接测试与连接字段同区，反馈真实结果；编辑影响连接的字段后旧测试结果标记失效。认证信息默认遮挡。 |
| 选择系统 | 机器标签只读、有锁图标、无机器编辑/删除/新增入口；系统列表支持既有添加、编辑、删除、排序，保留 Boot 标识与未配置提示。 |
| 系统详情 | 基本信息中将 EFI 启动项和 GRUB 启动项按启动链逻辑排列，自动识别或选择值保留后端真实 ID；显示继承连接提示、配置选项、独立环境变量区；不恢复已删除的连接认证、代理、远端工作目录或重复标题。 |
| 设置测试 | UnixBench 测试项/组合采用紧凑分类、多列、折叠及已选摘要；名称 14px，不靠缩小字号。正式轮数、预热及其它参数保留原语义。 |
| 运行设置 | 全局运行参数、并行机器数、结果目录、超时/调度参数按逻辑组织；短数字字段紧凑，单位显式。 |
| 检查运行 | 机器、系统、测试、任务数来自实际计划；正式轮次与预热分别显示，任务统计口径与后端一致。提交前显示真实结果目录和可能重启的影响。 |
| 运行记录 | 搜索、筛选、排序和分页保持一致；返回列表尽量恢复此前条件及滚动位置，避免重新加载导致选择丢失。 |
| 运行详情 | 运行摘要、阶段/任务列表、日志、结果文件相互独立更新；选中文件或日志过滤条件不因轮询被重置。 |
| 服务器文件选择 | 清楚标识远端机器及路径，文件/目录模式、隐藏文件、权限、路径校验按既有接口；不混淆本地与远端文件。 |

- EFI 和 GRUB 是不同层次的选择，不假定必定存在 GRUB 或每个 EFI 启动项对应同一 GRUB 菜单。是否启用、加载来源、默认值、可选项、系统关联及切换后的失效规则必须依据真实后端能力；手动值与自动探测值须区分，自动识别不得静默覆盖用户配置。
- 自动识别、批量操作、删除、取消或重试明确对象和影响。影响真实服务器或运行中的任务时，遵循权限和确认策略。保持操作后焦点、筛选、选中项合理；若删除当前项，移至邻近有效项。确认后避免双击/Enter 产生重复请求。
- MUST NOT：仅为 UI 演示发送重启、运行、取消、删除等真实操作，或为了让界面显得完整而造成功连接、测试结果、启动项、进度或后台能力。使用隔离 fixture 验证。

- 页面切换与返回按第 13.6 节执行：先遵循原有校验和未保存配置保护，再对已确认的视图切换提供一次视觉反馈；不得通过动效改变保存、提交、历史导航或远端任务的语义。

## 19. Page states, feedback and error recovery

| State | Presentation | Recovery / behavior |
| --- | --- | --- |
| Initial loading | 局部骨架屏，保留标题和结构 | 不显示误导性空态 |
| Refreshing | 保留上次有效内容，轻量更新提示 | 不清空表格、日志或焦点 |
| Empty | 中性图标 + 明确为何为空 | 真实可用的创建/配置入口 |
| No matching results | 显示筛选无结果 | 清除/修改筛选 |
| Partial failure | 仅失败区块提示，保留正常数据 | 对失败区域单独重试 |
| Offline / unavailable | 显示连接或数据获取异常 | 保留最后更新时间，恢复后刷新 |
| Permission denied | 清楚说明无权限 | 不显示无效操作，不绕过权限 |
| Error | 失败对象、原因（已知时）和可执行下一步 | 保留输入，允许安全重试 |
| Stale data | 标记数据可能过期及最后成功更新时间 | 恢复连接后按服务端状态同步 |

- 优先就地错误和 inline alert；非阻塞成功通知可短暂显示（建议 3–5s），错误与需操作警告持续可见或可关闭；Toast 不能是唯一错误记录。相同轮询错误聚合，不持续刷屏。对可访问状态使用适当 live region，避免每个日志字符或进度变化都被播报。
- 不把 `0`、空数组、`null`、请求失败与尚未加载混为一谈。未知数据显示 `—` 或 `N/A`，必要时注明原因。异常文案不披露内部堆栈、凭据或敏感路径；诊断信息如可安全展示则可展开/复制。
- 对长任务提供可感知的当前阶段和经过时间，而非以无限 Loading 掩盖异常；取消/重试入口只在真实能力和状态允许时出现。禁用关键按钮时解释原因。

## 20. Task lifecycle and asynchronous operations

### 20.1 State model

MUST：先检查后端调度器、状态枚举、事件及 API 契约，建立显式映射；下表是 UI 语义分组，不是要求修改后端状态枚举或新增未支持的能力。运行（run）、任务（task）、阶段（phase）、轮次（round）及预热（warm-up）各自独立，不将父子状态混用。

| UI semantic state | Display | Rules |
| --- | --- | --- |
| Queued / pending | 等待执行，中性 | 未开始，不显示已完成进度 |
| Connecting | 正在连接服务器，信息色 | 仅后端报告时显示 |
| Reboot / reconnect | 切换系统 / 等待重启 / 等待重连，信息色 | 预期 SSH 断开不等于失败 |
| Preparing / building | 环境准备 / 编译，信息色 | 仅显示真实阶段 |
| Warm-up / running | 预热 / 正式测试，信息色 | 轮次、系统、测试及实际进度 |
| Collecting | 正在收集结果，信息色 | 如果调度器支持 |
| Cancelling | 正在取消，信息色/警告 | 请求已接受不等于取消完成 |
| Succeeded | 执行成功，成功色 | 只由真实终态确认 |
| Failed | 执行失败，错误色 | 区分失败阶段、原因、可重试性 |
| Cancelled / skipped | 已取消 / 已跳过，中性色 | 按实际终态区分，不能标成功 |
| Unknown / stale | 状态未知 / 数据可能过期，警告 | 未知值保留原始安全标识，不假定运行或失败 |

- 前端网络状态与运行任务状态分别建模；轮询超时、浏览器离线、事件流断开，不改变最后确认的运行终态。展示最后成功同步时间及状态是否过期；重连或刷新后重新获取权威状态。
- 状态转换、终态、是否允许取消或重试由后端决定。乱序响应不得把较新状态覆盖为旧状态；若存在版本号/序列号使用它们，否则通过请求代次等策略拒绝过期结果。支持幂等的接口应使用幂等键；不支持时前端锁定重复操作并在不确定结果时查询后端，禁止盲目重试启动请求。
- 任务失败保留已生成的有效结果和日志，明确部分成功与整体结果；任务汇总和完成百分比按实际计数口径（含取消/跳过的处理）定义，不凭前端假设。

### 20.2 Progress, polling and logs

- 有可靠分母时提供已完成/总数及进度；无可靠分母时显示阶段和不确定进度。不能按客户端时间假造百分比或预估结束时间。已耗时用实际开始时间与结束时间（或当前时刻）计算，优先服务端时间信息。
- 轮询复用现有服务层；建议界面状态查询在 1–3s 基线范围内按实际需求、后端限制配置，失败指数退避（例如至 30s，带抖动并遵循 Retry-After）。同一资源不重叠轮询；非活动页降频或暂停，恢复时立即重新同步；终态停止不必要轮询。注意后端调度器自身的轮询参数与浏览器 UI 刷新频率不同，不因 UI 修改后端行为。
- 日志增量追加（已有 cursor/offset/event ID 时复用），处理重复、断流、乱序、截断和轮转；无增量接口时不自行假装支持。用户滚至历史处暂停自动跟随，提供“回到底部/恢复跟随”；搜索、过滤、复制及下载不影响任务。对高频日志合并渲染，不逐行播报，不自动将浏览器滚动移到日志区。
- 重启、SSH、编译、执行、采集等错误区分原因和恢复路径；对真实的机器重启、取消和重试严格保留既有权限及确认。刷新页面不应被当作取消服务端任务。

## 21. Data visualization and benchmark statistics

### 21.1 Chart selection and visual encoding

| Purpose | Recommended chart |
| --- | --- |
| 对比多个系统的同一测试项 | 分组横向柱状图；柱状图数值轴默认从 0 开始 |
| 多轮或历史趋势 | 折线图，时间/轮次顺序明确，缺失点断开 |
| 波动及分布 | 箱线图或散点图；标注样本数及统计方法 |
| 基线差异 | 带 0 轴的发散条形图，提供原值和差异 |
| 详细数值 | 数据表，可排序、查看或导出实际数据 |

- 图表背景、标题、图例、刻度、Tooltip 与全局字体和色彩一致；图表文字通常 12–14px，关键数据与表格对应。网格线使用浅色装饰 token，轴、标记、数据线与背景有必要的对比度。禁止 3D、无意义渐变、夸张动画、截断柱状图轴或用双轴制造趋势关联。
- 系列色按稳定系统 ID 或明确基准映射，跨图表保持一致；不是按当前排序或数组索引临时分配。推荐系列色顺序 `#2563EB`、`#0F766E`、`#7C3AED`、`#B45309`、`#0369A1`、`#BE185D`；超过可辨识数量时允许筛选、分面、小倍图，配合符号、线型、图例或直接标签。系列颜色不等同于成败语义。
- Tooltip 显示系统、测试项、指标、原始数值、单位、轮次或采样时间；支持键盘可访问的等效信息，不只依赖 hover 或颜色。图表提供表格/文本等效数据与明确的标题、图例、基准、统计口径。交互筛选、隐藏系列、缩放必须可重置。
- 不混合不同单位或不兼容指标；对数轴仅用于明确有意义的正数数据并标识。轴截断、归一化、过滤及聚合显式说明；不以视觉简洁为由隐去异常值。

### 21.2 Benchmark correctness and statistics

- MUST：原始指标、单位、方向（higher-is-better、lower-is-better、无方向）及统计口径来自测试插件/结果 schema；不能通过指标名字猜测方向。UnixBench 分数通常越高越好，延迟/耗时通常越低越好。未定义方向时仅展示客观变化，不标记提升或回退。
- 基线明确选择并标识，不默认把当前第一行或最高值当作基线。设基线为 B、当前值为 X：仅在 B > 0 且指标适合比率比较时计算原始变化率 `(X - B) / B × 100%`；对于 lower-is-better，改善率为 `(B - X) / B × 100%`。展示原始变化率还是改善率必须标注；基线为 0、缺失、非有限数或不适合比率时显示 N/A，可提供绝对差值。禁止除零、Infinity 或以 0 代替缺失值。
- 性能改善使用成功语义色、回退使用错误语义色、持平/未知使用中性；同时提供正负号及明确文字。持平阈值必须由指标配置或明确的分析约定提供，不凭 UI 设定统计显著性。阈值以内不代表统计等价，未经统计检验不宣称显著提升。
- 正式轮次与预热分开；默认不把预热、失败、跳过或缺失样本计入正式统计。任何异常值排除必须有明确规则、原因与原始数据保留。展示有效样本数 n、缺失/失败样本数及聚合方法。不同并发数、绑核/NUMA、内核、用户态、文件系统或测试版本的比较应标记条件差异，不能假定环境等价。
- 支持现有结果中的均值、中位数、最小/最大值、标准差、CV 等；样本标准差采用 n−1（n≥2），CV = 标准差 / 均值（适合比率尺度且均值为正时），否则 N/A；分位数、箱线图四分位算法、置信区间及几何平均的计算遵循统一后端或分析层契约并可追溯。不要擅自重新计算 UnixBench 综合 Index 或改变 benchmark 官方聚合方法。
- 默认显示精度：分数 1 位小数，百分比 2 位，时间/吞吐量根据量级及指标元数据选择适当精度，次数为整数；排序、筛选、聚合、导出使用未经显示舍入的原值。图表标题注明单位，切换单位不改原始数据。导出保留完整精度、单位、环境标识和统计口径（在现有导出能力允许时）。

## 22. Data tables, technical content and file views

- 表头使用真实字段名和单位；数值右对齐并启用 tabular-nums，文本左对齐，操作列稳定、可键盘操作。列宽根据内容设置 min/max；长 ID、路径、哈希可省略中间部分，提供可查看/复制完整值的方式，不依赖仅 hover 的 tooltip。
- 仅有实际排序/筛选能力时显示控件，数字按数值排序、时间按时间值排序而非格式化字符串；缺失值排序规则一致。分页默认建议 20 条，可提供 20/50/100 选择（如果数据模型与接口支持）；服务端分页必须区分总数与当前页数量，不能把仅加载的一页伪装成全量筛选结果。
- 批量选择区分当前页与全部匹配项，显示数量；跨页选择语义明确。列表数据变化时校正非法页码与失效选择。保留筛选、排序、分页，除非数据范围实际发生变化。表头 sticky、固定操作列仅在不遮挡焦点且不造成横向溢出时启用。

| Technical content | Display rules |
| --- | --- |
| IP / port / system ID | 普通表单用 UI 字体；保留原始值；端口与 IP 不插入装饰字符 |
| EFI / GRUB Entry | 展示可读描述与真实标识，区分 EFI 与 GRUB；长值可查看完整内容 |
| 内核版本 / 测试版本 | 不擅自解析或裁剪版本语义，复制原值 |
| SHA256 | 允许中间省略，完整 64 位十六进制字符串可查看/复制（按真实数据） |
| 文件路径 / 环境变量 | 保留大小写、空格、相对/绝对路径和转义语义；敏感值脱敏 |
| 时间 / 时长 | 明确日期、时间、时区；持续时间与绝对时间分开；运行中更新不抖动 |
| 日志 / 命令 | 等宽字体、可选中复制，保留换行，提供横向滚动/可选换行；禁止执行显示内容 |
| JSON / YAML / 文本预览 | 纯文本或安全高亮，支持长文本和大文件限制，禁止直接插入不可信 HTML |

- 日志等级使用文本与图标，ANSI 仅允许经过安全解析的必要颜色/样式，剥离危险控制序列、链接等非必要能力。不要为显示效果重写原始日志语义；敏感信息处理见第 27 节。
- 参数对比区分新增、删除、修改、未变及未知；标明比较对象、基线、配置版本和数据来源。导出不能遗漏筛选范围或让用户误以为导出全量；CSV 等面向电子表格的导出按安全规范防止公式注入。

## 23. UX writing, terminology and localization

默认语言为简体中文；使用简洁、准确、面向工程人员的动词与术语，不引入未经用户要求的语言切换或改变配置 schema。已有接口字段、命令、文件名、技术 ID 保持原样；显示文案与数据字段解耦。

| Canonical wording | Rule |
| --- | --- |
| 连接机器 / 连接测试 | 步骤及动作名称固定；不混用检测连接等名称 |
| 选择系统 / 系统标识 | 与系统实际名称区分 |
| EFI 启动项 / GRUB 启动项 | 两者分别命名；必要时显示 EFI Entry / GRUB Entry 原文 |
| 设置测试 / 测试项 / 测试组合 | 区分单项、组合及测试配置 |
| 正式轮数 / 预热 | 不将预热包含在正式轮数描述中 |
| 运行设置 / 检查运行 / 运行记录 | 顶部导航与步骤名称保持一致 |
| 远端工作目录 / 结果输出目录 | 区分远端工作目录与结果存放位置 |
| 选择文件 / 选择目录 | 根据目标类型选择动词，不混淆上传或本地浏览 |
| 正在取消 / 已取消 / 执行失败 | 状态文案与真实后端状态对应 |

- 按钮使用动宾结构，例如“连接测试”“添加变量”“重新连接”；不要用模糊的“处理”“确定”代替明确操作（普通确认语境除外）。删除确认写明对象及影响，错误提示描述失败对象、已知原因与下一步，不捏造诊断结论。
- 中文句子使用全角中文标点；技术命令、路径、版本、ID 使用原始 ASCII 符号。单位用明确的 `ms`、`s`、`MiB`、`MB` 等，不能混用十进制和二进制单位。日期建议 `YYYY-MM-DD HH:mm:ss` 并标注采用的时区；存储与传输遵循既有 ISO 8601/时间戳契约。数字格式与统计精度按第 21 节。
- 对未来国际化保留可提取的文案字典或现有 i18n 机制；禁止通过拼接中文片段构造依赖语序的文案。使用 Intl 或现有库做本地化；确保英文扩展、长文本、中文 IME 可用。页面声明正确 lang；不强行引入新 i18n 依赖。错误代码与显示文案分离，允许安全的详细信息查看。

## 24. Accessibility and inclusive interaction

目标为 WCAG 2.2 AA 的适用成功准则，不声称仅满足本文件列出的部分准则即已完成正式合规认证。遵循原生 HTML 优先和 WAI-ARIA APG 推荐模式；ARIA 属性不得与实际行为不一致。

- Landmark（header/nav/main 等）、合理标题层级、跳至主内容链接；所有交互控件有可访问名称，标签/说明/错误通过正确的原生关联或 ARIA 关联。表格有语义表头；图表及图标等提供适当等效文本。路由切换后合理更新文档标题及焦点，后台更新不抢焦点。
- 键盘可完成配置、选择、排序、提交、取消和文件选择；不依赖 hover、拖拽、双击或颜色作为唯一操作途径。Tab 顺序与视觉逻辑一致；不使用正数 tabindex；非 Modal 不困住焦点。自定义 Select、Dialog、Tabs、Tree 必须完整实现对应键盘模式。键盘焦点不被 sticky 遮挡，项目目标是完整可见。
- 普通文字 4.5:1、大号文字 3:1（WCAG 大号定义为约 24 CSS px 常规或约 18.67 CSS px 粗体）；适用的非文本控件状态及边界与相邻色至少 3:1。焦点至少清晰可见且满足适用对比度；若当前细边框与默认边框变化不易辨认，键盘焦点增加明确单层轮廓或其它可见标记，不能以美观为由隐藏。禁用控件等按 WCAG 适用例外处理。
- 文字缩放 200%、重排 320 CSS px、文字间距覆盖（行高 1.5 倍、段后间距 2 倍、字距 .12em、词距 .16em，适用语言与内容）分别测试。最小点击目标 24×24 CSS px 或允许的例外；项目优先 32px，触屏优先 44px。必要时支持 reduced-motion、forced-colors 及系统高对比度。
- Tooltip/Popover 内容满足适用的可关闭、可悬停及持续显示要求。状态通知按紧急程度选择 polite/assertive，不反复宣读高频更新；错误不能仅靠短暂 toast。自动化 axe 等检查作为辅助，必须结合人工键盘及屏幕阅读器抽查，不以工具零告警直接声称 AA 合规。

## 25. Frontend architecture, design tokens and CSS engineering

### 25.1 Architecture boundaries

- MUST：先识别仓库真实技术栈、目录、组件、路由、状态管理、API 客户端、构建/测试命令。以下分层是职责建议，不强制重构目录或引入新框架。现有项目中如有等效模块，应复用并逐步收敛。

| Layer | Responsibility |
| --- | --- |
| Theme / tokens | 颜色、字体、间距、尺寸、圆角、阴影、动效、层级唯一来源 |
| UI primitives | Button/Input/Select/Dialog/Table 等通用组件，负责语义和交互，不绑定 benchmark 接口 |
| Shared patterns | FormField、PageShell、WizardLayout、StatusBadge、EmptyState、FilePicker 等通用组合 |
| Feature components | 机器连接、系统编辑、UnixBench 配置、运行记录/详情等业务组件 |
| Page / route | 页面编排、数据加载、路由和权限，不重复实现原子控件 |
| Domain / services | 数据契约、API、任务状态映射、统计与配置转换；不与视觉实现耦合 |

- 优先最小可复用抽象，公共组件采用明确 variants（size、intent、state 等），避免为单个页面复制组件或创建大量近义 prop。控件必须支持禁用、加载、错误、键盘、焦点、可访问标签等适用语义；组件默认值符合本规范。
- 业务逻辑、调度策略、统计计算与 UI 分离；不要在 JSX/模板渲染时调用副作用或改变配置。服务端权限、路径校验、启动/停止语义仍由服务端负责，前端只辅助展示和验证。
- 新增/修改依赖需说明必要性、复用可能、体积、兼容性、安全与许可证影响；不要混用多个组件库、图标库、状态库或 CSS 方案。使用已有包管理器及锁文件，禁止无关依赖升级和大范围格式化。

### 25.2 Token governance

- 全局 token 分为 primitive（基础色值/数值）、semantic（主色、表面、状态、焦点）、component alias（控件实际使用语义）。允许现有 `--ui-*` 与 `--text-*` 作为语义层；不要求为迁移而复制两套变量。组件消费语义 token 而非随意色值；不要用 token 名称掩盖多个实际来源。
- 统一命名、定义位置和作用域；第 8、14 节为参考实现，应合并到同一现有主题入口；渐变与动效 token 遵循第 13 节，不在页面层复制。页面、步骤、Tab、Drawer 的过渡通过第 14 节语义别名及共享导航/浮层组件统一实现，禁止独立硬编码。新增 token 必须解释用途、检查已有等价 token、补充组件状态及测试。变更全局 token 评估所有消费组件影响，视觉基准变更需明确审核。
- 布局建议额外提供 `--ui-header-height: 4rem`、`--ui-sidebar-width: 14rem`、`--ui-plan-width: 18.75rem`、`--ui-shell-gap: var(--ui-space-6)`、`--ui-page-padding: var(--ui-space-6)`；断点由现有主题或 CSS media query 统一维护，不能指望普通 CSS custom property 直接用于 media query 条件。图表序列色与状态色分离。主题扩展优先通过语义 token 重映射，为未来深色主题预留结构，但本版本不要求实现深色主题。
- CSS 使用现有模块、作用域或 utility 体系；全局 reset 仅限必要行为，避免全局选择器误伤第三方/原生组件。禁止滥用 `!important`、深层选择器、任意 z-index、重复覆盖及行内硬编码（动态几何或第三方 API 等确需情况应说明）。优先逻辑属性、flex/grid、min-width:0 和可扩展高度；CSS 断点、token 与组件 API 维持一致。

## 26. State management, API and data contracts

- 区分服务端权威数据（运行状态、结果、远端文件）、表单草稿（未提交配置）、页面状态（筛选、选中、展开）、派生状态（计划摘要）与敏感临时状态（密码等）。只为实际需要共享的状态提升作用域，不复制同一实体到多份独立状态。派生数据纯计算，服务端状态按真实接口重新同步。
- API 通过现有统一客户端/服务层处理请求、错误、超时、取消和必要的认证；保留 HTTP/API 实际状态、错误码及类型，不把异常转换为成功空列表。请求体字段、默认值、null 与缺省、枚举、路径及单位必须保持契约。跨版本未知字段不能被无意丢弃（按现有序列化规则）。
- 按请求类型设置合理超时（短查询与 SSH 探测、远端操作不同），沿用后端明确的超时设置，不以 UI 超时推断服务端任务取消。支持 AbortController 或现有取消机制；页面卸载、对象切换时取消无效查询；对不可取消的请求忽略过期响应。前端取消 HTTP 等待不等于取消远端执行。
- 查询在暂时性网络错误时可有限重试并指数退避；尊重 Retry-After、避免并发重试风暴。验证失败、无权限、无效配置等不盲目重试。创建运行、重启、删除等有副作用请求不得自动重试，除非后端明确支持安全幂等；结果不确定时先查询实际状态。提交按钮防重复，后台操作需真实确认后更新 UI。
- 缓存按资源 ID、配置版本、筛选、机器/系统范围等隔离；切换资源不展示上一资源的敏感数据或过期响应。变更后准确失效相关缓存，保留不相关数据；乐观更新只用于可安全撤销且已有能力的操作，失败时回滚。API 响应数据不可信，必要时进行运行时 schema 校验，未知状态采用安全降级。

## 27. Security, privacy and operational safety

- MUST：不在 URL/query、localStorage/sessionStorage、浏览器日志、错误提示、分析事件、截图 fixture、测试基准或导出中保存/暴露 SSH 密码、私钥、访问令牌及敏感环境变量。仅在必要流程短暂持有敏感值；显示默认遮挡，明确复制/显示操作。按现有合法配置持久化契约处理服务端秘密，不把前端脱敏误认为安全存储。
- 不信任任何远端文件名、路径、日志、测试输出、错误信息、配置文件或 API 文本。默认安全文本渲染；禁止无条件 `innerHTML`/`dangerouslySetInnerHTML`、eval 或执行不可信输出。确需富文本时使用已审核的安全解析和上下文适当的净化策略，限制链接协议、ANSI/HTML 功能；CSP 等防御纵深沿用部署架构。CSV 导出防止公式注入，同时提供适合机器读取的无损格式（如已有）。
- 保持现有认证、授权、CSRF（适用 cookie 会话时）、CORS、会话、安全传输及服务端校验，不放宽策略来解决 UI 报错。客户端隐藏/禁用控件不是权限校验。不得因为连接测试失败而自动忽略 SSH 主机密钥检查；显式配置该选项时提供清晰风险提示，不更改用户原有默认值。
- 文件选择器路径访问、目录穿越、符号链接、权限及文件类型由服务端校验；前端检查仅为体验。命令参数与环境变量按既有接口传输，不拼接到不受控 shell 中；预览不得自动执行。真实重启、启动、停止、删除、下载等操作明确对象与权限，测试一律优先使用 mock 或隔离环境，禁止在生产机器上执行未经授权的破坏性验证。
- 用户可见错误与诊断日志应脱敏，允许保留安全的关联 ID 和必要上下文；日志下载与结果文件权限沿用后端。避免把主机真实 IP、账号、路径等敏感部署信息引入公开设计样图、测试快照或遥测。发现安全回归优先修复或明确阻塞发布。

## 28. Frontend performance and browser compatibility

### 28.1 Performance targets

| Metric | Target / scope |
| --- | --- |
| LCP | ≤2.5s |
| INP | ≤200ms |
| CLS | ≤0.1 |
| Search debounce | 200–300ms，仅适合高频查询 |
| High-volume logs / tables | 分块、分页或虚拟滚动，按测量决定阈值 |
| Polling | 无重叠、终态停止、后台降频 |

前三项采用 Core Web Vitals good 阈值，适用于有代表性的真实页面访问第 75 百分位（桌面/移动分开）；没有现场数据时使用可复现本地/CI 测试作为代理，不能宣称已达到现场指标。其他值为项目建议；记录测试浏览器、视口、数据量、网络及设备条件。

- 不因局部日志或状态变化重新渲染整个配置页面；按实际框架拆分更新范围，批量处理高频事件，避免不必要的深拷贝、重复统计、重复请求。长列表按测量采用分页/虚拟滚动（兼顾键盘、搜索、复制与可访问性）；大文件限制预览体积并提供安全下载。必要时延迟加载重型图表或预览模块，避免首屏加载无关依赖。
- 避免阻塞主线程的大量解析与同步计算；有测量依据时使用分块、worker 或后端聚合。避免 hover 改变布局尺寸、频繁布局读写、未预留异步内容空间；骨架与真实内容结构尽量一致。
- 清理事件监听器、定时器、observer、网络订阅、Object URL 及无用缓存；切换运行记录或组件卸载后不得继续追加旧日志。新增性能优化需验证正确性与可访问性，不能用吞掉日志或减少实际结果来达成指标。

### 28.2 Compatibility

- 桌面优先，兼顾 Windows、macOS、Linux。建议验证当前及前一稳定版 Chromium/Edge、Firefox、Safari（Safari 以 macOS/iOS 实际环境为准）；真实支持矩阵由项目已有目标环境决定，不凭空声称测试通过。无环境时记录未验证项。
- 使用标准 Web API 和现有构建目标；必要时特性检测、渐进增强或现有 polyfill。系统字体、原生控件、滚动条、输入法、剪贴板和下载行为可能跨平台不同，确保功能等价而非像素完全一致。不要依赖仅 Chromium 支持的能力作为唯一关键路径。

- 页面切换默认采用共享 CSS，View Transition API 仅作为渐进增强并做特性检测；验证 API 不支持或过渡跳过时导航仍正常。对长报告、日志、固定表头和大列表验证过渡不引入明显合成/快照开销（第 13.6 节）。

## 29. Automated testing, visual regression and release acceptance

### 29.1 Test layers and fixtures

| Layer | Minimum coverage for relevant changes |
| --- | --- |
| Unit / domain | 计划计数、配置序列化、状态映射、指标方向、基线 0/缺失值、统计精度 |
| Component | 默认/hover/focus/disabled/error/loading/selected 等状态；键盘、IME、长文案、事件和验证 |
| Integration / API mock | 成功、失败、超时、权限、空结果、过期响应、重复提交、轮询终态、断线恢复 |
| E2E | 五步配置、返回保留值、EFI/GRUB 选择、测试计划与提交一致、运行记录、运行详情、远端文件选择 |
| Accessibility | 自动检查 + 手动键盘、焦点、缩放、重排、对比度及必要的屏幕阅读器检查 |
| Visual regression | 关键页面及关键控件状态的浏览器基准截图 |
| Performance | 代表性数据量下的首屏、交互、日志持续更新、资源清理 |

- 使用项目已有测试框架及脚本；如已有 Playwright 可使用 `expect(page).toHaveScreenshot()`，没有则评估现有等效能力，不擅自引入大型测试框架。选择器优先 role/name/label，必要时使用稳定 test ID；不要依赖内部 CSS 类名、任意 sleep 或脆弱 DOM 层级。断言实际行为、值、请求与状态，而不仅是截图。
- 使用确定性、脱敏 fixtures；冻结时间、随机数据及异步响应顺序，覆盖极端长路径、多个系统、空列表、大量日志、缺失/失败样本、不同并发值。不得使用真实凭据、真实生产任务或真实重启作为默认自动化测试。验证取消请求中与真实取消终态区别、SSH 重启阶段与网络故障区别。

- 导航动效测试覆盖真实 route/step/tab 切换、快速连续导航、浏览器前进/后退、减少动画、无 API 回退、校验阻止导航与轮询不重播。断言最终视图、焦点、草稿、滚动位置、请求次数及无陈旧动画状态；避免依赖固定 sleep 或逐帧像素完全一致。可选 View Transition 路径另验证中断和失败不重复提交。

### 29.2 Visual reference workflow

- 已确认 AI 效果图是人工设计参考，不是可直接进行像素比较的浏览器基准；如仓库已保存这些图，优先读取对应页面最新版本（可建议存放 `docs/design-system/references/`，但不假设路径已存在）。新版系统详情以包含 GRUB Entry 且无重复连接设置的版本为准。若参考图与明确功能契约或本规范冲突，以契约、用户明确要求与可访问性为准。
- 首次实现：人工对照效果图检查布局、层级、浅蓝顶栏、字体、留白、控件及交互；通过验收后保存实际浏览器截图作为回归 baseline。固定 viewport、DPR、浏览器/OS、字体、fixture、语言、时区与动画状态；等待确定的页面就绪条件。截图不得含真实密码、密钥、IP 等敏感数据。
- 最低基准视口建议 1440×900、1280×800、1920×1080，另测 1024、768、390 及 320 CSS px 宽度的适用布局；对 200% 文字缩放另行检查。关键截图覆盖连接机器、系统列表、系统详情、设置测试、运行设置、检查运行、运行记录、运行详情、文件选择器及下拉/焦点/异常状态。
- 视觉差异阈值由现有工具与稳定性测量确定，不随意放宽掩盖回归；仅遮罩真正不稳定且非待测的内容（例如动态时间），不得遮罩受测状态。更新 baseline 必须说明原因并经视觉复核，不能为了让 CI 通过而直接接受所有差异。跨 OS 字体差异可分别维护基准。

### 29.3 Final checklist for coding agents

除第 10、15 节之外，按改动范围执行以下验收；未适用项标记 N/A，不能用 N/A 掩盖未执行的必要验证。

- [ ] 页面复用统一 AppShell/布局原语，所有浅蓝顶栏一致；短输入框尺寸合理，无无意义留白、全页横向溢出或焦点遮挡。
- [ ] 语义 token、图标、文案及状态映射有唯一来源；复用公共组件，不引入平行设计系统。
- [ ] 五步流程、配置保存与恢复、条件字段、自动识别、EFI/GRUB、只读机器标签及计划计数符合真实业务。
- [ ] 运行进度、轮次、取消、失败、重连、日志和刷新恢复均基于实际状态，无伪造进度、过期响应覆盖或重复提交。
- [ ] 图表与表格单位、基线、方向性、统计口径、缺失数据和导出一致，原始结果可追溯；不伪造统计显著性。
- [ ] 适用页面状态、错误恢复、键盘、屏幕阅读器、文本缩放、重排、对比度、触控与减少动画要求已检查。
- [ ] API 契约、权限、密钥检查默认值、数据格式不变；不泄露凭据，不执行不可信日志或未经授权的真实服务器操作。
- [ ] 代表性数据下无明显性能回退、重复轮询、监听器泄漏或布局跳动；兼容性检查结果如实记录。
- [ ] 渐变/动效改动通过第 13.5 节验收；正常动效、快速连续操作、减少动画偏好及静态截图分别验证，不影响真实进度、焦点或请求语义。页面切换另执行第 13.6.4 节，验证固定外壳、正常/快速导航、历史恢复和减少动画/兼容性回退。
- [ ] 执行项目已有 lint、类型检查、单元/组件/集成/E2E 测试及 build（按存在的命令及影响范围），保存必要截图及测试证据。
- [ ] 最终报告列出改动文件、复用组件/token、行为影响、运行过的命令及结果、视觉验收证据、未验证项及原因。不得把计划执行的测试描述成已通过。

## 30. Governance, agent workflow and evolution

- 本文件使用 MUST / MUST NOT 表示强制约束，SHOULD 表示默认推荐（确需偏离时说明理由），MAY 表示可选。表中的范围和建议值在功能、可访问性或布局约束需要时可以合理选择；禁止未经说明增加新的全局视觉标准。以实际项目业务契约和用户明确要求为先；新需求涉及 API、权限、数据结构、配置默认值或任务调度改变时，明确区分于纯 UI 改造，不在视觉任务中顺带改变。
- Agent 修改前先阅读当前目录适用指令和本文件相关章节，再检查实际代码、参考图及测试。跨页面重构先确定共享 token/组件及迁移范围；小修复保持最小改动，不因本规范存在而自动重写整个项目。用户未要求实现的图表、i18n、深色主题等只预留规范，不凭空新增产品功能。
- 发生冲突时：优先遵守安全、数据正确性、可访问性与现有业务契约；之后遵循最新明确设计决定和对应组件/页面专项规范，最后是通用建议。不要复制过时 CSS 或以效果图推断业务逻辑。发现无法调和的冲突或未知契约时，说明并尽量完成无冲突的部分。
- 本文件目前为单文件完整规范。随着项目增长，可经用户同意将详细规范拆分至 `docs/design-system/`（typography、colors、layout、components、icons、motion、interaction、visualization、accessibility、ux-writing、page-patterns）和 `docs/engineering/`（frontend、task-lifecycle、security、performance、testing）；届时根 AGENTS.md 保留强制规则和按任务阅读索引，不能仅移动文件却遗漏阅读要求。现阶段不要假定这些文档已存在。
- 维护原则：新增页面沿用现有共享组件；修改设计系统时同步相关 token、组件测试、视觉基准与规范；删除过时规则或明确替代关系，避免同一参数多处矛盾。遵循仓库已有代码评审和 CI 流程。不要为了符合文档而引入未经需求确认的功能或依赖。

## 31. References

- [Ant Design — Font](https://ant.design/docs/spec/font/)
- [Material Design 3 — Typography](https://m3.material.io/styles/typography/overview)
- [W3C — WCAG 2.2](https://www.w3.org/TR/WCAG22/)（重点关注 1.4.3、1.4.4、1.4.10、1.4.12、2.4.7、2.5.8）

- [Ant Design — Customize Theme](https://ant.design/docs/react/customize-theme/)（组件 tokens 与尺寸体系）
- [Ant Design — Motion](https://ant.design/docs/spec/motion/)（动效参考）
- [W3C — WAI-ARIA APG Patterns](https://www.w3.org/WAI/ARIA/apg/patterns/)（控件语义与键盘交互）
- [W3C — Combobox Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/)
- [W3C — Modal Dialog Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)
- [W3C — Non-text Contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast)（WCAG 1.4.11）
- [W3C — Focus Not Obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum)（WCAG 2.4.11）

- [W3C — Understanding WCAG 2.2](https://www.w3.org/WAI/WCAG22/Understanding/)（包括重排、目标尺寸、拖拽、文字间距与状态消息）
- [W3C — Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow)
- [W3C — Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)
- [web.dev — Web Vitals](https://web.dev/articles/vitals)（Core Web Vitals 阈值及测量方法）
- [Playwright — Visual comparisons](https://playwright.dev/docs/test-snapshots)（浏览器截图回归参考）
- [OWASP — Cross Site Scripting Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [OWASP — Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
- [OpenAI — AGENTS.md guidance](https://developers.openai.com/codex/guides/agents-md)（项目指令组织；读取预算依实际工具版本配置）

行业参考用于设计方法、无障碍和工程实践；具体 AutoBench token、布局断点、轮询频率、统计展示精度及测试策略为项目约定。遵循本文件不自动意味着第三方认证或真实性能达标。
