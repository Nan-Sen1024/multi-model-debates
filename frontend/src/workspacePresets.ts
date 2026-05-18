import type {
  CollaborationMode,
  ParticipantConfig,
  SessionWorkspaceView,
  WorkspaceDiscoveredSkill,
} from "./types";

export const AGENCY_STARTER_PACK_PATHS = [
  ".codex/skills",
  "C:\\Users\\Nan\\.codex\\skills",
  "D:\\game\\mycode\\devPython\\agency-agents",
  "D:\\game\\mycode\\devPython\\agency-agents\\integrations\\antigravity",
];

export interface WorkspaceStarterPackRecommendation {
  id: string;
  label: string;
  summary: string;
  status: "connected" | "available";
  configured_sources: string[];
  supporting_skill_names: string[];
}

export interface WorkspaceTaskPresetRecommendation {
  id: string;
  label: string;
  summary: string;
  mode: CollaborationMode;
  template_id: string;
  topic: string;
  supporting_skill_names: string[];
}

export interface WorkspaceTeamPresetRole {
  alias: string;
  role_desc: string;
  supporting_skill_names: string[];
}

export interface WorkspaceTeamPresetRecommendation {
  id: string;
  label: string;
  summary: string;
  roles: WorkspaceTeamPresetRole[];
}

export interface WorkspacePresetBundle {
  starter_pack: WorkspaceStarterPackRecommendation;
  task_presets: WorkspaceTaskPresetRecommendation[];
  team_presets: WorkspaceTeamPresetRecommendation[];
}

function normalizeText(value: string | null | undefined): string {
  return (value || "").trim().toLowerCase();
}

function normalizePath(value: string | null | undefined): string {
  return normalizeText(value).replace(/\\/g, "/");
}

function skillCorpus(skill: WorkspaceDiscoveredSkill): string {
  return [skill.name, skill.description, skill.summary].map(normalizeText).join(" ");
}

function includesAny(text: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(text));
}

function isProductSkill(skill: WorkspaceDiscoveredSkill): boolean {
  return includesAny(skillCorpus(skill), [
    /product/,
    /workspace/,
    /workbench/,
    /priorit/,
    /roadmap/,
    /产品/,
    /工作台/,
    /信息架构/,
  ]);
}

function isReviewSkill(skill: WorkspaceDiscoveredSkill): boolean {
  return includesAny(skillCorpus(skill), [
    /review/,
    /critic/,
    /qa/,
    /审查/,
    /评审/,
    /回归/,
    /risk/,
  ]);
}

function isImplementationSkill(skill: WorkspaceDiscoveredSkill): boolean {
  return includesAny(skillCorpus(skill), [
    /fix/,
    /implement/,
    /debug/,
    /test/,
    /builder/,
    /engineer/,
    /修复/,
    /实现/,
    /测试/,
    /验证/,
  ]);
}

function filterSkillNames(
  skills: WorkspaceDiscoveredSkill[],
  predicate: (skill: WorkspaceDiscoveredSkill) => boolean,
): string[] {
  return skills.filter(predicate).map((skill) => skill.name);
}

function collectConfiguredStarterPackSources(workspace: SessionWorkspaceView | null): string[] {
  const sources = workspace?.capabilities?.skill_sources || [];
  return sources
    .map((source) => source.path)
    .filter((path) =>
      AGENCY_STARTER_PACK_PATHS.some((candidate) =>
        normalizePath(path).includes(normalizePath(candidate)),
      ),
    );
}

export function buildWorkspacePresetBundle(
  workspace: SessionWorkspaceView | null,
): WorkspacePresetBundle {
  const discoveredSkills = workspace?.discovered_skills || [];
  const configuredStarterPackSources = collectConfiguredStarterPackSources(workspace);
  const productSkillNames = filterSkillNames(discoveredSkills, isProductSkill);
  const reviewSkillNames = filterSkillNames(discoveredSkills, isReviewSkill);
  const implementationSkillNames = filterSkillNames(discoveredSkills, isImplementationSkill);

  const taskPresets: WorkspaceTaskPresetRecommendation[] = [];
  const teamPresets: WorkspaceTeamPresetRecommendation[] = [];

  if (productSkillNames.length > 0) {
    taskPresets.push({
      id: "analyze_repo_product_lens",
      label: "Analyze Repo with Product Lens",
      summary: "把仓库理解收敛到 Workspace、Task、Run、Review、Provider，并给出下一步产品化建议。",
      mode: "code_workspace",
      template_id: "analyze_repo",
      topic: "通读并分析这个仓库的结构、核心模块、产品定位、信息架构问题、信任回路缺口和后续建议。",
      supporting_skill_names: productSkillNames,
    });
    teamPresets.push({
      id: "product_owner_implementer_pair",
      label: "PO + Implementer",
      summary: "第一位参与者负责产品诊断，第二位参与者负责把建议收敛成最小实现与验证路径。",
      roles: [
        {
          alias: "Product_Owner",
          role_desc: "从 Workspace、Task、Run、Review、Provider 视角做产品诊断，明确主路径、信任回路和下一步优先级。",
          supporting_skill_names: productSkillNames,
        },
        {
          alias: "Implementer",
          role_desc: "结合代码与运行链路，把产品建议转成最小可交付实现、验证步骤和风险说明。",
          supporting_skill_names: [],
        },
      ],
    });
  }

  if (reviewSkillNames.length > 0) {
    taskPresets.push({
      id: "review_changes_skill_assisted",
      label: "Review Changes with Skill Support",
      summary: "组织多模型围绕风险、回归和改动质量做结构化评审。",
      mode: "peer_review",
      template_id: "review_changes",
      topic: "评审当前改动或方案，输出主要问题、风险、回归点和建议优先级。",
      supporting_skill_names: reviewSkillNames,
    });
    teamPresets.push({
      id: "reviewer_challenger_pair",
      label: "Reviewer + Challenger",
      summary: "一位参与者做主评审，另一位参与者专门补充反例、遗漏风险和验证缺口。",
      roles: [
        {
          alias: "Reviewer",
          role_desc: "主导结构化评审，给出问题清单、严重级别和回归风险。",
          supporting_skill_names: reviewSkillNames,
        },
        {
          alias: "Challenger",
          role_desc: "专门寻找主评审遗漏的边界条件、失败路径和验证缺口。",
          supporting_skill_names: reviewSkillNames,
        },
      ],
    });
  }

  if (implementationSkillNames.length > 0) {
    taskPresets.push({
      id: "fix_with_verification_loop",
      label: "Fix with Verification Loop",
      summary: "围绕真实缺陷执行修复、验证和结果归纳，而不是只给静态建议。",
      mode: "code_workspace",
      template_id: "fix_or_implement",
      topic: "定位并修复当前问题，说明根因、影响文件、验证步骤和剩余风险。",
      supporting_skill_names: implementationSkillNames,
    });
    teamPresets.push({
      id: "fixer_validator_pair",
      label: "Fixer + Validator",
      summary: "一位参与者聚焦实现修复，另一位参与者聚焦测试、命令验证和结果审查。",
      roles: [
        {
          alias: "Fixer",
          role_desc: "负责定位根因、修改代码并保持改动范围可控。",
          supporting_skill_names: implementationSkillNames,
        },
        {
          alias: "Validator",
          role_desc: "负责命令执行、测试验证、回归风险和完成度审查。",
          supporting_skill_names: implementationSkillNames,
        },
      ],
    });
  }

  if (!taskPresets.length && discoveredSkills.length > 0) {
    taskPresets.push({
      id: "analyze_repo_discovered_skills",
      label: "Analyze Repo from Discovered Skills",
      summary: "先基于已发现技能理解这个 Workspace 的重点模块、边界和后续工作方向。",
      mode: "code_workspace",
      template_id: "analyze_repo",
      topic: "通读并分析这个仓库的结构、核心模块、关键约束，以及已发现技能最适合切入的任务方向。",
      supporting_skill_names: discoveredSkills.slice(0, 3).map((skill) => skill.name),
    });
  }

  return {
    starter_pack: {
      id: "agency_starter_pack",
      label: "Agency Starter Pack",
      summary: "把 Codex skills 和 agency-agents 角色库接成可扫描、可解释、可复用的 Workspace 资产。",
      status: configuredStarterPackSources.length > 0 ? "connected" : "available",
      configured_sources: configuredStarterPackSources,
      supporting_skill_names: discoveredSkills.slice(0, 4).map((skill) => skill.name),
    },
    task_presets: dedupeById(taskPresets),
    team_presets: dedupeById(teamPresets),
  };
}

function dedupeById<T extends { id: string }>(items: T[]): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.id)) {
      return false;
    }
    seen.add(item.id);
    return true;
  });
}

export function applyWorkspaceTeamPreset(
  participants: ParticipantConfig[],
  preset: WorkspaceTeamPresetRecommendation,
): ParticipantConfig[] {
  const nextParticipants = [...participants];
  while (nextParticipants.length < preset.roles.length) {
    nextParticipants.push({
      custom_id: `Model_${nextParticipants.length + 1}`,
      model_ref: "",
      provider_id: undefined,
      role_desc: "",
    });
  }

  return nextParticipants.map((participant, index) => {
    const nextRole = preset.roles[index];
    if (!nextRole) {
      return participant;
    }
    return {
      ...participant,
      custom_id: nextRole.alias,
      role_desc: nextRole.role_desc,
    };
  });
}
