# UI redesign decisions

Decisions taken while replacing the "lab notebook" frontend with the current
design. Each entry says what was decided and why, so a later change can tell
a deliberate choice from an accident.

## Problems being fixed

1. **Low contrast.** One token, `--paper-edge: #ebe7db`, was the border of
   every input, button and card. Measured against the `#fffdf7` card it was
   **1.22:1**, far under the 3:1 WCAG 1.4.11 requires for a control boundary.
   Hint text (`--text-muted: #86847c`) measured **3.68:1**, under the 4.5:1
   body-text minimum.
2. **Setup was one long scroll** with every form expanded and no signal of
   what was done.
3. **Draft values were loaded as real values.** With no saved config, `GET
   /config` returns a draft seeded from the bundled langlearn demo, and the
   form prefilled it. A blind Save wrote the demo's agent, model and test
   command into the user's own config.
4. **AI-smells:** a handwriting font (`--font-hand`, Comic Sans on most Linux
   installs) for every heading, a ruled-paper background, fake tape strips on
   cards, and em-dash-clause copy throughout.

## Visual direction

**Neutral engineered, light and dark equally first-class.** Chosen over a
dark-first "arena scoreboard" and a light "broadsheet" look. Cool grey and
blue neutrals, 4px radii, system sans for UI, monospace for identifiers,
paths and every number. No web fonts (no dependency, works offline).

The showdown feel comes from structure rather than decoration:

- a **Standings** table with placement per feature,
- a colour **rail** per contender that matches its bars in the charts,
- a brand mark of two crossed gladii, one per contender, in the first two
  series colours (`components/BrandMark.tsx`). Chosen from four sketches
  (versus cut, health bars, crossed gladii, arena floor) as the clearest
  colosseum-duel reference. It replaced an earlier mark of two bars, which
  read as a chart icon. It deliberately borrows no Pokémon artwork, which is
  trademarked. The mark is decorative (`aria-hidden`), since the wordmark
  beside it names the app,
- showdown vocabulary where it is accurate: "New showdown", "Contenders",
  "Head to head", "Standings". Not applied where it would obscure meaning
  (buttons still say "Start run", "Save configuration").

## Design tokens

- **All colour, type size, spacing and radius values live in
  `frontend/src/styles/tokens.css`.** Re-theming means editing that one file.
- **Border split into two tokens.** `--border` is decorative (separators,
  panel edges). `--border-strong` is the boundary of anything interactive and
  must hold 3:1. The old theme's single border token is what caused problem 1.
- **Measured contrast** (every pair checked with a script, not estimated):

  | Pair | Light | Dark |
  | --- | --- | --- |
  | `--border-strong` on `--surface` | 3.54:1 | 3.71:1 |
  | `--border-strong` on `--surface-2` | 3.30:1 | 3.39:1 |
  | `--text` on `--surface` | 18.3:1 | 14.7:1 |
  | `--text-3` (hints) on `--surface` | 5.85:1 | 5.79:1 |
  | `--accent-ink` (links) on `--surface` | 7.90:1 | 8.33:1 |

  Light `--border-strong` started at `#8b94a1` but measured 2.86:1 on
  `--surface-2` (panel headers, inset rows), so it was darkened to `#808996`.
- **Kept names:** `--series-1..3`, `--de-emphasis` and `--grid`.
  `charts/palette.ts` and its tests reference the series names literally.
- **Renamed:** chart SVGs moved from `--surface-1`, `--text-primary`,
  `--text-secondary`, `--text-muted` to `--surface`, `--text`, `--text-2`,
  `--text-3`.
- **Theme selection** keeps the existing three-block pattern: light on
  `:root`, dark via `prefers-color-scheme`, and `[data-theme]` overrides that
  win in both directions.
- **Theme switch** in the header: system, light, dark
  (`components/ThemeSwitch.tsx`, `hooks/useTheme.ts`, `lib/theme.ts`).
  - *Default is system*, which follows the OS setting.
  - *Stored in the browser's `localStorage`* under `agentshowdown.theme`, not
    on the server. It is a per-device display preference and the app has no
    user accounts, so a backend endpoint and a file on disk would add
    surface for no benefit. The trade-off: the choice does not follow you to
    another browser or machine.
  - *Choosing system removes the key* rather than storing `"system"`, so
    clearing a choice leaves nothing behind.
  - *Blocked storage is handled*: reads fall back to system, and a failed
    write still applies the choice for the current page view.
  - *No flash on load*: a small inline script in `index.html` stamps the
    saved theme before first paint. It duplicates the storage key, so a test
    fails if the two drift. The backend serves the frontend without a
    Content-Security-Policy, so the inline script is not blocked; adding a
    CSP later would need a hash or nonce for it.
  - *Buttons with `aria-pressed`*, not radios: each is a one-click action,
    and the icons are hidden from assistive tech since every button carries
    a label.
- A visible `:focus-visible` ring was added; the old sheet had none.

## Modularity

- **Styles split by layer** under `frontend/src/styles/`: `tokens`, `base`,
  `layout`, `controls`, `panel`, `scoreboard`, `chart`. `styles.css` only
  imports them, so `main.tsx` did not change.
- **Presentational primitives in `components/ui/`:**
  - `Panel`: collapsible section with step number, heading, summary line and
    status chip.
  - `StatusChip`: tone plus text; colour never carries meaning alone.
  - `Notice`: inline message whose ARIA role is derived from its tone
    (`danger`/`warn` become `alert`, `ok`/`neutral` become `status`), so
    callers cannot mismatch them. Not in the original plan; added because the
    same role/class pairing was hand-written in every component.
  - `Field`: label + control + hint, replacing about 25 hand-rolled copies.
    The caller passes the same `id` to the control rather than `Field`
    cloning children, which would be fragile.
- **Pure helpers in `lib/`:** `lib/jobs.ts` (status tone, formatting,
  placement) is shared by the standings table and the contender cards so
  they cannot disagree. `lib/config.ts` holds the draft-clearing function; it
  was moved out of the component file because React Fast Refresh requires
  component files to export only components.
- **Editors render bare bodies.** `ConfigEditor`, `WorkspacePicker`,
  `FeatureEditor`, `IssueImporter`, `RunLauncher` and `SandboxControls` no
  longer draw their own card and heading. Views own the chrome, so an editor
  can be placed in any container. Editors report what they loaded through
  optional callbacks (`onDocChange`, `onSecretsChange`) so the view can show
  status without fetching the same data twice.
- Inline `style` is used only for data-driven series colours. Layout tweaks
  went into classes (`field-grid--bottom`, `checks--field`).

## Setup page

- **Panels in order:** 01 Repository, 02 Configuration, 03 Secrets,
  04 Features. Each header shows a status chip (`selected` / `required`,
  `saved` / `not saved` / `read-only preset`, `N stored` / `none stored` /
  `sbx unavailable`, `N defined` / `none yet`).
- **Import from GitHub issues moved inside the Features panel** as a nested
  disclosure, rather than being its own panel as first planned. Importing
  produces features, so it belongs with them, and it removes a panel that
  only appears for repos with a GitHub remote.
- **Only Repository opens by default, and only when no repo is selected.** A
  configured workspace lands on a fully collapsed page you can read at a
  glance.
- **`defaultOpen` is read once.** Otherwise selecting a repository would flip
  the prop and snap the panel shut while someone is still working in it.
- **Built on native `<details>`/`<summary>`**, not a button toggling a hidden
  region: keyboard and screen-reader behaviour come from the platform, and
  collapsed forms stay mounted and keep their typed state.
- **Readiness chip** at the top: `Ready to run` or `N of 3 ready`, counting a
  selected repo, a saved config, and at least one feature. Secrets are not
  counted, because some agents (for example a local Ollama endpoint) need no
  stored secret.
- **Secrets panel lists what is stored.** `GET /secrets` and `fetchSecrets()`
  already existed but nothing called them; the panel was just a button. It
  shows names and state only, never values, and re-reads when the store
  dialog closes.

## Draft values

- **Cleared on a draft:** `github.repo`, `agent.sbx_agent`, `agent.model`,
  `run.test_command`, `run.lint_command`, and the per-agent `agents` profile
  map (not shown in the form, but it would have been written on save).
- **Kept:** `base_branch`, `timeout_minutes`, `max_concurrency`, `verify_on`,
  and the plumbing the form does not show (`status_file`, poll and liveness
  intervals). These are generic rather than demo-specific, and number inputs,
  selects and a required `base_branch` (the API enforces `min_length=1`)
  cannot usefully be blank.
- **Placeholders are generic examples**, never copied from the demo:
  `pytest -q`, `ruff check .`, `claude`, `main`.
- **Two placeholders come from real data:**
  - *Repository* uses the remote detected from the selected repo. The
    server's draft fills `github.repo` from the repo's own git remote, not
    from the demo, so it is a genuine suggestion. It falls back to
    `owner/repo`.
  - *Default model* uses the chosen agent's `default_model` from
    `GET /agents`, with its `known_models` as a datalist. This is the pattern
    `AgentSpecFields` already used.
- **Frontend-only fix.** `GET /config` still returns the draft; the form
  decides what to show. Chosen over changing the endpoint, to keep backend
  behaviour and its tests unchanged.
- Covered by a regression test that fails on the old prefill behaviour
  (verified by temporarily reverting the fix) and passes on the new code.

## Compare page

- **One Standings table** replaces the "All values" table. It is still the
  only `<table>` on the page and is still the accessibility backstop for the
  charts: every charted value is in it as text.
- **Placement is per feature.** Different tasks are not a match, so numbering
  restarts at 1 for each feature. Order: outcome (succeeded, then in flight,
  then failed), then passing tests, then wall-clock time.
- **Missing values sort last in both directions.** Reversing a sort must not
  float "no data" to the top as if it were the longest or largest.
  Unreported values render as `—` in the table and "not reported" on cards,
  never as zero.
- **Columns are sortable** with real buttons and `aria-sort` on the header.
- **Head to head charts still receive the full job roster**, not the repo
  filtered subset, matching previous behaviour. The palette is assigned from
  the full roster, so colours never renumber when filtering.
- **The repository filter moved to the page header**; it used to render below
  the job list.
- **Sandboxes is a collapsed panel** at the bottom, because it is occasional
  and contains a destructive action.
- **Only one `role="status"` region in the shell.** The header's
  live/reconnecting indicator is plain text, so the preflight notice remains
  the page's single status region.

## Copy

- Removed em-dash clauses and filler from user-visible strings, for example
  `Draft — nothing saved at X yet` became `No configuration saved at X yet.`,
  and `Cloned into X — now in use` became `Cloned into X. Now in use.`
- Code comments were left alone; only rendered text changed.
- No emoji.
- The criterion and environment-variable "Remove" buttons keep plain labels.
  Adding `aria-label="Remove criterion 1"` made the button match the same
  label query as the "Criterion 1" input.

## Test changes

- Updated for intentional changes only: the launcher heading (`New
  comparison` became `New showdown`) and the draft notice wording.
- New suites: `Panel`, `StatusChip`, `Notice`, `Field`, `SecretsManager`,
  `Standings`, `SetupView`, `lib/jobs`, `lib/config`, plus three new
  `ConfigEditor` cases (the draft regression, the placeholder sources, and
  save-state reporting).
- Totals: frontend 124 to 161 tests; line coverage 95%, branch coverage
  88.7% (thresholds are 80%).

## Not done

- **No visual check in a real browser.** No headless browser is installed on
  the development machine, and installing one was not requested. The
  production bundle builds, and the contrast numbers above are computed from
  the token values, but the rendered pages have not been viewed.
- No backend change, no new dependency, no theme toggle.
