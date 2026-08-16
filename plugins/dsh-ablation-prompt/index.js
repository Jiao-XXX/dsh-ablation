import Schema from '@deepseek-ai/schemastery'

export const name = 'ablation-prompt'

// `systemPrompt` is a hard dependency here, unlike the optional seams an
// approval plugin probes with ctx.get(): a prompt-axis cell whose prompt
// override silently did not apply is not a degraded cell, it is a cell
// mislabelled as Minimal while showing the model Standard's prompt. Failing to
// mount is the correct outcome -- a void cell is recoverable, a quietly wrong
// datapoint is not.
// `agents` is needed only by the shadow feature, which must register on an
// agent-scoped context. Declaring it unconditionally keeps the dependency
// honest: this plugin never works without an agent plane once shadowing is
// configured, and a half-applied prompt manipulation is the failure mode that
// produces a mislabelled cell.
export const inject = ['systemPrompt', 'agents']

export const Config = Schema.object({
  text: Schema.string()
    .default('You are a helpful software engineer assistant.')
    .description('The complete system prompt text.'),
  section: Schema.string()
    .default('ablation:complete-persona')
    .description(
      'Section name. It must NOT be "deployment:persona": the SystemPrompt '
      + 'service registers that name unconditionally in its own constructor, so '
      + 'reusing it collides. Any other name works because a complete section '
      + 'suppresses every other section regardless of what it is called.',
    ),
  order: Schema.number()
    .default(0)
    .description('Prompt order; 0 is the persona slot, the first section a model reads.'),
  suppressRuntimeContext: Schema.boolean()
    .default(true)
    .description('Drop the dynamic runtime-context snapshot, as the Minimal preset does.'),
  complete: Schema.boolean()
    .default(true)
    .description(
      'Register the complete-prompt section at all. Set false to use this '
      + 'plugin purely as a section shadower, leaving the deployment prompt in '
      + 'place except for the sections named in shadowSections.',
    ),
  shadowSections: Schema.array(Schema.string())
    .default([])
    .description(
      'Prompt section names to blank out while leaving their owning plugins '
      + 'mounted. Tool plugins name their guidance `tool:<toolName>` '
      + '(tool:read, tool:bash, tool:goal, tool:subagent_fork, ...), so this '
      + 'removes a tool\'s PROSE without removing the tool from the catalog -- '
      + 'the separation `disabled: true` cannot express. Registered on the '
      + 'agent scope, where a same-named section shadows the global one.',
    ),
})

/**
 * Register one `complete: true` prompt section, making it the entire system
 * prompt.
 *
 * This exists because the Minimal preset achieves its one-line prompt through
 * `@deepseek-ai/dsh-persona` mounted on the AGENT plane, where a scoped
 * section shadows the deployment-wide one. A headless profile has no agent
 * plane: every row lands at the host plane, where that plugin's own
 * registration collides with the prompt registry's built-in persona section
 * (it hardcodes `deployment:persona`). Registering a differently named
 * complete section reaches the same end state -- one section, everything else
 * suppressed -- through the supported API.
 *
 * The harness rejects a second effective complete section, so mounting this
 * plugin twice, or alongside a complete persona, fails loudly rather than
 * silently picking a winner.
 */
export function apply(ctx, config = {}) {
  const {
    text, section, order, suppressRuntimeContext, complete, shadowSections,
  } = Config(config)

  if (complete) {
    ctx.effect(() => ctx.systemPrompt.section({
      name: section,
      order,
      text,
      complete: true,
    }), 'ablation-prompt.section()')
  }

  if (suppressRuntimeContext) ctx.systemPrompt.suppressRuntimeContext()

  if (shadowSections.length === 0) return

  // Shadowing is per-agent by necessity: the prompt registry rejects a second
  // GLOBAL registration of an existing section name, and only a scoped section
  // shadows a global one of the same name (system-prompt/src/index.ts:375).
  ctx.effect(() => ctx.on('agent/created', ({ agent }) => {
    agent.ctx.effect(() => {
      const disposers = shadowSections.map(name => agent.ctx.systemPrompt.section({
        name,
        order,
        text: '',
      }))
      return () => { for (const dispose of disposers) dispose() }
    }, 'ablation-prompt.shadow()')
  }), 'ablation-prompt.agent-created()')
}
