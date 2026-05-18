import {
  applyWorkspaceTeamPreset,
  buildWorkspacePresetBundle,
} from "./workspacePresets";
import type { SessionWorkspaceView } from "./types";

describe("workspace preset recommendations", () => {
  test("derives product-led task and team presets from discovered skills", () => {
    const workspace: SessionWorkspaceView = {
      root_path: "D:/repo/demo",
      display_name: "demo-repo",
      repo_fingerprint: "fp",
      scan_excludes: [],
      selected_paths: [],
      index_status: "ready",
      summary: "demo",
      capabilities: {
        skill_sources: [
          {
            path: "C:/Users/Nan/.codex/skills",
            source_type: "local",
            label: "Codex",
            recursive: true,
            enabled: true,
          },
        ],
        mcp_servers: [],
        agent_defaults: {
          mode: "tool_loop",
          max_steps: 6,
          can_write: false,
          allowed_skills: [],
          allowed_mcp_servers: [],
          memory_scope: "workspace_shared",
        },
        participant_overrides: {},
      },
      discovered_skills: [
        {
          name: "ai-rd-workbench-product-owner",
          description: "Productizes local AI workbenches",
          summary: "Focus on Workspace, Task, Run, Review and Provider.",
          path: "C:/Users/Nan/.codex/skills/ai-rd-workbench-product-owner/SKILL.md",
          source_type: "local",
          source_label: "Codex",
        },
      ],
      files: [],
      tree: [],
    };

    const bundle = buildWorkspacePresetBundle(workspace);

    expect(bundle.starter_pack.label).toBe("Agency Starter Pack");
    expect(bundle.starter_pack.status).toBe("connected");
    expect(bundle.task_presets.map((preset) => preset.id)).toContain("analyze_repo_product_lens");
    expect(bundle.team_presets.map((preset) => preset.id)).toContain("product_owner_implementer_pair");
  });

  test("fills missing participant slots when applying a team preset", () => {
    const participants = [
      {
        custom_id: "Model_A",
        model_ref: "openai/gpt-5.4",
        provider_id: "provider-openai",
        role_desc: "",
      },
    ];

    const next = applyWorkspaceTeamPreset(participants, {
      id: "product_owner_implementer_pair",
      label: "PO + Implementer",
      summary: "test",
      roles: [
        {
          alias: "Product_Owner",
          role_desc: "产品诊断",
          supporting_skill_names: ["ai-rd-workbench-product-owner"],
        },
        {
          alias: "Implementer",
          role_desc: "实现与验证",
          supporting_skill_names: [],
        },
      ],
    });

    expect(next).toHaveLength(2);
    expect(next[0]).toMatchObject({
      custom_id: "Product_Owner",
      model_ref: "openai/gpt-5.4",
      provider_id: "provider-openai",
      role_desc: "产品诊断",
    });
    expect(next[1]).toMatchObject({
      custom_id: "Implementer",
      model_ref: "",
      role_desc: "实现与验证",
    });
  });
});
