import { CollaborationMode } from "./types";

export interface TaskTemplateOption {
  id: string;
  label: string;
  blurb: string;
  icon: string;
  mode: CollaborationMode;
  defaultTopic: string;
}

export const PRIMARY_TASK_TEMPLATES: TaskTemplateOption[] = [
  {
    id: "analyze_repo",
    label: "Analyze Repo",
    blurb: "基于本地代码工作区理解仓库结构、关键模块、风险点和后续建议。",
    icon: "🧭",
    mode: "code_workspace",
    defaultTopic: "通读并分析这个仓库的结构、核心模块、风险点和后续建议。",
  },
  {
    id: "fix_or_implement",
    label: "Fix or Implement",
    blurb: "围绕真实任务修改代码、执行命令并交付验证结果。",
    icon: "🛠️",
    mode: "code_workspace",
    defaultTopic: "定位并修复当前问题，提交改动思路、影响文件和验证结果。",
  },
  {
    id: "review_changes",
    label: "Review Changes",
    blurb: "组织多模型对变更、方案或仓库片段做结构化评审。",
    icon: "🧾",
    mode: "peer_review",
    defaultTopic: "评审当前改动或方案，输出主要问题、风险和建议。",
  },
  {
    id: "compare_approaches",
    label: "Compare Approaches",
    blurb: "让多个模型并行比较方案、取舍和执行路径。",
    icon: "⚖️",
    mode: "brainstorm",
    defaultTopic: "比较几种可行方案的优缺点、成本和推荐路径。",
  },
];

export const DEFAULT_TASK_TEMPLATE_ID = PRIMARY_TASK_TEMPLATES[0].id;

export const MODE_OPTIONS: Array<{
  value: CollaborationMode;
  label: string;
  blurb: string;
}> = [
  { value: "chat", label: "自由聊天", blurb: "顺序接力式多模型对话" },
  { value: "brainstorm", label: "头脑风暴", blurb: "并行生成创意，再汇总" },
  { value: "code_collaboration", label: "代码协作", blurb: "多角度审查与改进建议" },
  { value: "code_workspace", label: "代码工作区", blurb: "本地仓库 + @alias 开发流" },
  { value: "data_analysis", label: "数据分析", blurb: "结构化发现、风险与建议" },
  { value: "debate", label: "辩论", blurb: "立场交锋，支持共识检测" },
  { value: "werewolf", label: "狼人杀", blurb: "私有身份、阶段推进、胜负判定" },
  { value: "murder_mystery", label: "剧本杀", blurb: "角色调查与推理" },
  { value: "undercover", label: "谁是卧底", blurb: "描述、投票、淘汰" },
  { value: "mock_trial", label: "模拟法庭", blurb: "阶段化庭审流程" },
  { value: "role_play", label: "角色扮演", blurb: "共享世界观与剧情推进" },
  { value: "socratic_dialogue", label: "苏格拉底问答", blurb: "持续追问与洞察沉淀" },
  { value: "peer_review", label: "多模型评审", blurb: "Producer/Reviewer 迭代" },
  { value: "mock_interview", label: "模拟面试", blurb: "提问、追问、作答与建议" },
  { value: "story_chain", label: "故事接龙", blurb: "多角色续写故事" },
  { value: "negotiation", label: "模拟谈判", blurb: "私有立场与协议生成" },
];

export const PROVIDER_TYPES = [
  "openai",
  "anthropic",
  "google",
  "groq",
  "mistral",
  "xai",
  "ollama",
  "lm_studio",
  "vllm",
  "openrouter",
  "litellm",
  "gateway",
  "custom",
];

export const API_FORMATS = ["openai-completions", "anthropic-messages"];

export const AUTH_TYPES = ["iam", "api_key", "bearer", "helper", "oauth", "adc"];
