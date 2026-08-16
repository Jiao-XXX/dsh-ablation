import Schema from '@deepseek-ai/schemastery'

export const name = 'ablation-anchor'

// `agents` supplies the per-agent scopes this plugin must reach, and `tools`
// owns restrict(). Both are hard injects: a catalog-axis cell whose
// restriction never installed shows the model all 25 tools while reporting
// itself as a 2-tool cell, which is worse than not running.
export const inject = ['agents', 'tools']

export const Config = Schema.object({
  mode: Schema.union(['anchored', 'permanent'])
    .default('anchored')
    .description(
      'permanent: hold the allowlist for the entire session (axis A1). '
      + 'anchored: hold it for request #1 only, then lift (axis A1-prime, the '
      + 'dsh-anchored-standard replica).',
    ),
  bootstrapTools: Schema.array(Schema.string())
    .default(['bash', 'str_replace_editor'])
    .description('The tool names that stay visible while the restriction is held.'),
  promoteOn: Schema.union(['either', 'tool-call', 'assistant-message'])
    .default('either')
    .description('Which durable session event lifts an anchored restriction.'),
  residentDeny: Schema.array(Schema.string())
    .default([])
    .description(
      'Tool names to keep hidden AFTER promotion. Use it when a tool exists '
      + 'only to make the bootstrap pair available: without it the resident '
      + 'catalog would be the comparison baseline plus that extra tool, and an '
      + 'anchored cell would differ from the Standard cell in two ways at once '
      + 'instead of one.',
    ),
  rootAgentsOnly: Schema.boolean()
    .default(true)
    .description(
      'Skip subagents. A delegated child inherits its own catalog decision; '
      + 'anchoring it too would confound the axis with a delegation change.',
    ),
  debug: Schema.boolean()
    .default(false)
    .description(
      'Trace phase transitions to stderr. A headless run leaves stderr empty on '
      + 'success, so this is legible without disturbing the stdout result.',
    ),
})

/** Whether the session log already shows the action that ends the bootstrap phase. */
function hasPromoted(events, promoteOn) {
  const wanted = promoteOn === 'tool-call'
    ? ['tool/call']
    : promoteOn === 'assistant-message'
      ? ['assistant/message']
      : ['tool/call', 'assistant/message']
  return events.some(event => wanted.includes(event.type))
}

/**
 * Hold the model-facing tool catalog to an allowlist.
 *
 * Why a restriction rather than disabling the tool rows in the overlay: a
 * disabled tool row takes its PROMPT SECTIONS with it. Measured on this
 * testbed, collapsing the catalog to 2 tools by disabling 14 rows also drops
 * the system prompt from 4063 to 315 characters -- so an overlay-built
 * "tool-catalog axis" silently moves the prompt axis too, and any difference it
 * produces cannot be attributed. `ctx.tools.restrict()` filters the catalog the
 * model sees while every plugin stays mounted and every prompt section stays
 * registered, which is the only way axis A1 is a single-axis manipulation.
 *
 * The restriction MUST be installed on an agent-scoped context: the registry
 * rejects a context-global restriction outright, because it would mask every
 * agent in the process rather than the one under test.
 *
 * Promotion is derived from the durable session log rather than from a
 * process-local flag, so a resumed session lands in the same phase it left --
 * the phase is a property of the conversation, not of this process.
 */
export function apply(ctx, config = {}) {
  const { mode, bootstrapTools, promoteOn, residentDeny, rootAgentsOnly, debug } = Config(config)
  const trace = debug
    ? (...parts) => { console.error('[ablation-anchor]', ...parts) }
    : () => {}

  ctx.effect(() => ctx.on('agent/created', ({ agent }) => {
    const isRoot = ctx.agents.roots().includes(agent)
    trace('agent/created', { isRoot, mode })
    if (rootAgentsOnly && !isRoot) return

    agent.ctx.effect(() => {
      // Already past the bootstrap phase on a resumed session: never re-anchor,
      // or a resume would show the model a catalog it had already outgrown.
      if (mode === 'anchored' && hasPromoted(agent.session.events, promoteOn)) {
        return () => {}
      }

      let lift = agent.ctx.tools.restrict({ allow: bootstrapTools })
      let stopWatch = () => {}
      let stopExec = () => {}

      // Promotion REPLACES the bootstrap allowlist with the resident filter
      // rather than simply removing it, so the resident catalog can be made
      // identical to the comparison baseline even when a tool had to be
      // re-enabled to supply the bootstrap pair.
      let residentLift = () => {}
      const promote = (why) => {
        if (!lift) return
        lift()
        lift = undefined
        if (residentDeny.length > 0) {
          residentLift = agent.ctx.tools.restrict({ deny: residentDeny })
        }
        trace('promoted -> resident catalog', { why, residentDeny })
      }

      if (mode === 'anchored') {
        // WHY THE LIFT HAPPENS HERE AND NOT ONLY IN agent/pre-step:
        //
        // `Agent.preStep()` calls `systemPrompt.assemble()` -- which is what
        // produces the step's tool array -- BEFORE it dispatches the
        // `agent/pre-step` waterfall (agent-loop/src/agent.ts:230 vs :235).
        // A restriction lifted from inside that waterfall therefore lands one
        // step LATE: the step about to run was already assembled under the old
        // catalog. Measured on a two-step task, promotion fired correctly at
        // step 2 and the session still logged a single 2-tool header, because
        // the turn ended before a third assembly could observe the change.
        //
        // `tools/pre-execute` runs while step 1's tool call is dispatched --
        // after request #1 is complete, and before step 2 is assembled -- so
        // the promotion is visible in exactly the request the replica claims.
        if (promoteOn !== 'assistant-message') {
          stopExec = agent.ctx.on('tools/pre-execute', (exec, next) => {
            promote('tools/pre-execute')
            return next()
          }, { prepend: true })
        }
      }

      if (mode === 'anchored') {
        // `agent/pre-step` is a WATERFALL: this listener observes and must call
        // next(), returning its result. Failing to do so would veto the whole
        // downstream chain including the step itself.
        //
        // prepend so the lift happens before any listener that derives the
        // request from the catalog; a promotion that landed after derivation
        // would take effect one request later than it reports.
        stopWatch = agent.ctx.on('agent/pre-step', (payload, next) => {
          const promoted = hasPromoted(agent.session.events, promoteOn)
          trace('pre-step', {
            turn: payload?.turn,
            step: payload?.step,
            held: Boolean(lift),
            promoted,
            events: agent.session.events.length,
          })
          // Backstop for the assistant-message trigger and for a resumed
          // session that promoted in an earlier process. One step late is
          // correct here: there is no earlier seam for "the model replied".
          if (promoted) promote('agent/pre-step')
          return next()
        }, { prepend: true })
      }

      return () => {
        stopWatch()
        stopExec()
        residentLift()
        if (lift) lift()
      }
    }, 'ablation-anchor.restriction()')
  }), 'ablation-anchor.agent-created()')
}
