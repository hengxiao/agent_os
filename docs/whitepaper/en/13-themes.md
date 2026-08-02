# Theme System: Contract & Six Themes

> Chapter 13 · Status: Implemented (motion playback layer designed but not yet implemented — see §6) · Sources: `docs/DEBUG-UI-THEMES.md`, `docs/DEBUG-UI-MOE.md`, `agent_os/src/agent_os/host/web/static/js/themes.js`, `.../js/components/mascot.js`, `.../css/themes/`, `.../tests/themes-contract.test.mjs`, `.../tests/smoke-theme.test.mjs`

## 1. Overview

The theme system is the skinning architecture of the web host layer (`agent_os/src/agent_os/host/web/static/`). It formalizes "a skin" as a **three-layer contract** — semantic tokens, copy keys, and motion names — plus an optional, wholly detachable mascot layer. The six built-in themes (`classic`/`moe`/`terminal`/`blueprint`/`ink`/`pixel`) are data packages under that contract; component code exists exactly once (`docs/DEBUG-UI-THEMES.md:12`). The system lives entirely in the browser: the backend exposes no theme API, theme state never enters a run, a signal, or the WAL, and it is therefore orthogonal to the kernel's determinism engineering (Chapter 00, §6.1).

## 2. Motivation & Background (Why)

The system grew out of a proposal for "a second skin": the moe design doc (`DEBUG-UI-MOE.md`) originally set out to give the debug console a cute variant. During design, the author noticed that the differences between skins collapse into **three enumerable things**: token mapping + copy table + mascot/motion layer (`DEBUG-UI-THEMES.md:11`). Once differences are enumerable, the right move is not to build another skin but to formalize the differences as a contract — a theme becomes a pluggable data package. Four concrete tensions shaped the system:

- **Demo surface vs. engineering credibility.** The debug console is Agent OS's demo surface (breakpoints / stepping / intervention are hard technology), and design reviews, external demos, and personal use call for different temperaments (`DEBUG-UI-THEMES.md:15`). But the default tool must stay serious — so `classic` is the default and moe is an explicitly switched option (`DEBUG-UI-MOE.md:151`), never imposed.
- **Number of skins vs. maintenance cost.** If every skin duplicates the components, every fix must be written N times and behavior will drift (`DEBUG-UI-MOE.md:155`, risk row "maintaining two UIs drifting apart"). The answer: pin components to a single copy and push all differences down into data packages.
- **Open extension vs. quality floor.** Themes are meant to be open to community contribution (`DEBUG-UI-THEMES.md:17`), which requires a programmatic gate against half-finished work — the contract is both the extension point and the quality gate.
- **Skinning capability vs. engineering constraints.** The web frontend holds to "no build step, zero dependencies" (`DEBUG-UI-MOE.md:47`: the mascot is pure CSS/SVG, no image assets), which rules out CSS-in-JS and theme compilers, leaving exactly one technical route: CSS custom properties + the cascade.

Why not in the kernel or the backend: theming is pure presentation. Keeping it outside the runtime guarantees that switching themes can never contaminate replay, audit, or debugging semantics — the same placement decision as the "thin host" layering (Chapter 00, §3.1).

## 3. Problem Statement (What It Solves)

1. **Skin drift**: had the moe variant been built as a second page / second component set, every subsequent component fix would have to be written twice; within one iteration the two UIs would diverge (`DEBUG-UI-MOE.md:155`).
2. **Half-finished themes shipping**: if a theme's css omits `--danger`, failed states on some page become "a missing piece" — no color, no contrast — discoverable only by manual walkthrough.
3. **Pastel contrast failure**: the initial moe value `--ok: #5ec9a7` on a cream background falls below the 4.5:1 accessibility red line (`DEBUG-UI-MOE.md:80`); conscientious hand-tuning inevitably regresses.
4. **Single-channel color dependence**: if states are distinguished by color alone, color-blind users cannot tell done from failed from aborted.
5. **Copy translation swallowing technical originals**: if cute copy replaced raw error text, user trust in the tool collapses — "trust rests on 'this sprite never lies to me'" (`DEBUG-UI-MOE.md:26`).
6. **Temperament is not shareable**: switch the console to a theme, copy the link to a colleague, and they see the default theme — the deep link loses the "same temperament" (`DEBUG-UI-THEMES.md:68`).
7. **Themes leaking into unvetted pages**: a theme walkthrough-checked only on the debug page, applied app-wide, leaves visual holes on Runs/Skills pages.

## 4. Design & Mechanism (How)

### 4.1 Three-Layer Contract Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Components (status-pill / debug-view / inbox / lab …)         │
│   consume only: var(--semantic-token) · copy(key) ·           │
│   mascotHtml(expr) — theme ids are forbidden in component     │
│   code (contract test #6, static scan)                        │
├──────────────────────────────────────────────────────────────┤
│ Contract layer (js/themes.js)                                 │
│   CONTRACT_TOKENS (43 variables, themes.js:25-40)             │
│   COPY_KEYS (classic table is canonical, 69 keys, :43)        │
│   motion names × 5: bp-hit / step / resume / run-done /       │
│   intervene                                                   │
│   registration check: css not loaded or token missing →       │
│   refuse registration + console.warn                          │
├──────────────────────────────────────────────────────────────┤
│ Theme package = css/themes/<id>.css + js/copy/<id>.js         │
│   classic · moe · terminal · blueprint · ink · pixel          │
│   mascot registry (mascot.js:138-161): mochi · sprite8        │
└──────────────────────────────────────────────────────────────┘
```

(The architecture mirrors the three-layer diagram in `DEBUG-UI-THEMES.md:21-33`; zero-branch components are a hard constraint, not a guideline.)

### 4.2 Token Contract & Registration Validation

Every theme must assign all 43 contract variables under its `[data-theme="<id>"]` rule: base 9 (`--bg-0..3`, `--line`, `--line-strong`, `--fg-0..2`), status 5, signal 6, permission 4, typography 19 (fonts 2 + sizes 6 + spacing 7 + radii 4) — not one fewer (`themes.js:25-40`; the list is exactly WEB-UI.md §3's existing semantic variables). At registration, `sheetTokens()` extracts the theme's declaration block from the loaded stylesheets (`themes.js:108-129`); missing variables or an unloaded css means refusal plus `console.warn` (`themes.js:133-153`) — "preventing half-finished themes from going live" (`DEBUG-UI-THEMES.md:45`). In node test environments without the `styleSheets` API the runtime check is skipped, and completeness is asserted by the contract test parsing the css source directly (`themes.js:6-8`); both validation paths share the same contract list.

**Trade-off: refuse registration instead of backfilling at runtime.** A missing token has no sane fallback — filling it with classic's value would produce a region that "doesn't look like the theme," which is worse than the theme not loading at all. Better the whole theme stays off (`DEBUG-UI-THEMES.md:144`: "rather fall back than ship half-finished").

### 4.3 Copy Contract: A Translation Layer, Not a Replacement Layer

`COPY_KEYS` takes the classic table as canonical (69 keys covering status phrases, empty states, confirmations, Skill Lab, escalation cards — see `js/copy/classic.js`); every theme table must cover the same key set. Components read values via `copy(key)`, resolved through three fallback levels: **effective theme → classic → the raw key itself** (`themes.js:245-251`). The core rule is the **technical-text exemption**: raw errors, raw statuses, and tool parameters are always rendered verbatim and never enter the copy table (`DEBUG-UI-THEMES.md:50-51`). Theme copy is a "translation layer" — e.g. moe's aborted phrase is `先到这里喵(aborted)`, with the original kept in parentheses (asserted at `smoke-theme.test.mjs:262`).

**Trade-off: a missing copy key only warns, it does not block registration** (`themes.js:147-150`) — deliberately asymmetric with the token rule. A missing copy key has a classic fallback and the page does not break; a missing token has no fallback and leaves a visual hole. Different consequences, different gate strengths.

### 4.4 Switching, Persistence & Scope Fallback

```
startup initTheme():  URL (?theme=) > localStorage > classic (themes.js:223-232)
                      a URL hit is also persisted — deep links share "the same temperament"
switch  applyTheme(): requestedId → localStorage (agent-os.theme)
                      → hash ?theme= sync (replaceState, no routing; classic omits the param)
                      → resolveEffective(page): unvetted scope → forced classic
                        (themes.js:168-173)
                      → <html data-theme="<effective>"> → CSS variable cascade, instant app-wide
```

The implementation deliberately separates `requestedId` (the user's choice, the persisted object) from `effectiveId` (what actually applies on the current page, `themes.js:161-162`): a scope fallback does not overwrite the user's choice, and the original choice resumes automatically on vetted pages. `syncTheme()` re-resolves on route change (`app.js:339`). The TopBar picker is purely data-driven: it renders from the registry, each item carrying a three-color swatch read from the theme css (`--bg-0`/`--fg-0`/`--live`, `themes.js:254-260`).

**Trade-off: a `data-theme` attribute + CSS cascade instead of JS-driven skinning.** Switching re-renders nothing and components are oblivious to themes; it also makes the "no build, zero dependencies" constraint hold naturally. The cost is that theme expressiveness is capped at what CSS variables can say — but the §4.2 contract is exactly that range, so constraint and capability are consistent.

### 4.5 The Mascot Layer: A Second, Detachable Abstraction

The mascot is an independent layer, not an in-component branch: components only call `mascotHtml(expr)` at fixed slots (e.g. the control-bar slot at `debug-view.js:441`), and the layer itself reads the current theme's `mascot` declaration — under a `null`-mascot theme (classic/terminal/blueprint/ink) it returns an empty string, with no `if` in any component (`mascot.js:164-177`). Inside the layer sits the `MASCOTS` registry: each mascot = { name, exprs, sprite }, where the sprite is a pure-SVG `<symbol>` sprite sheet whose colors all come from the theme css class rules — zero color values inside the SVG (`mascot.js:21-22`). The expression mapping `mascotStateFor` derives from the session snapshot (running/paused/done/failed, `mascot.js:12-19`) and is shared by both mascots.

`pixel`'s `sprite8` is the **second instance** of this abstraction — the same `MascotLayer` interface with a different 8-bit sprite sheet, proving the layer is replaceable (`DEBUG-UI-THEMES.md:117`, asserted at `smoke-theme.test.mjs:424-436`).

### 4.6 The Six-Theme Catalog

| Theme | Temperament | Signature mappings | Mascot |
|---|---|---|---|
| `classic` | Serious engineering (default; contract reference implementation) | the status quo as a theme, values copied verbatim from tokens.css (`classic.css:2`) | none |
| `moe` | Cute (sakura-cream pastel) | Mochi state incarnation; cat-emoji status pills; copy translation layer | mochi |
| `terminal` | Terminal geek (phosphor green on black) | ASCII double-line frames; inverted paused line; shell-flavored copy | none |
| `blueprint` | Engineering drawing (blueprint blue + grid) | dashed drawing frames, title blocks, stamp-style statuses; `APPROVED(done)` | none |
| `ink` | Ink wash (rice paper + a single vermilion accent) | vermilion seal-stamp statuses; ink-stroke pause indicator; terse classical copy | none |
| `pixel` | 8-bit retro game | budget bars = HP/MP; breakpoint = checkpoint flag; `LEVEL CLEAR!` | sprite8 |

(`DEBUG-UI-THEMES.md §3.1-3.6`; all six are `scope: "app-wide"`, `themes.js:48-104`.)

### 4.7 Motion Profiles (Designed; Playback Layer Not Implemented)

The contract says components invoke only named motions (`bp-hit`/`step`/`resume`/`run-done`/`intervene`), and each theme declares a level per name: `full` / `subtle` / `instant`, with `prefers-reduced-motion` forcing `instant` (`DEBUG-UI-THEMES.md:55-57`). **Current implementation: all five motion names in all six themes are registered as `subtle`** (e.g. `themes.js:54`); the motion playback layer has not landed, and contract test #5 (motion-degradation assertions) is pending (`DEBUG-UI-THEMES.md:141`, Executive Summary §8). The scattered `@keyframes` in theme css files (moe breathing/entry, terminal scanlines, etc.) do not go through the named-motion channel.

## 5. Effects & Verification (Results)

**Contract test** `tests/themes-contract.test.mjs` (runs directly under node, no browser) iterates the registry and asserts five property groups per theme:

1. **Token completeness**: all 43 contract variables defined and non-empty in the css source (`themes-contract.test.mjs:94-100`);
2. **Contrast**: programmatic WCAG relative-luminance computation — 30 critical pairs (body text / status / signal & permission colors × bases) ≥ 4.5:1, 4 dim-tier pairs ≥ 3:1 (`themes-contract.test.mjs:77-114`); a theme author who changes a color gets instant red;
3. **Dual encoding**: status elements carry both a color hook (`data-status`/`data-on`) and a text/icon channel (status words / ● / ▶) — no dependence on the color-vision channel alone (`themes-contract.test.mjs:116-135`);
4. **Copy-key completeness**: copy tables cover all 69 keys (`themes-contract.test.mjs:137-139`);
5. **Zero component branches**: static scan of `js/components/` forbidding theme-id literals, `data-theme` attributes, and theme-id comparisons (`themes-contract.test.mjs:142-162`; `terminal` is exempt from the literal rule because the word collides with an icon name — only its comparison forms are banned).

**Smoke test** `tests/smoke-theme.test.mjs` covers runtime behavior in 12 scenario groups: registration negatives (a ghost theme whose css is not loaded is refused and does not pollute the registry), startup precedence (URL > localStorage > classic, with URL hits persisted), unknown-theme fallback to classic, picker rendering and wiring, four themes' copy voices (terminal `[halted](paused)`, blueprint `APPROVED(done)`, ink `驻(paused)`, pixel `CLEAR!`), MascotLayer appearing/disappearing with the theme, debug-console integration (Mochi appears in the control bar under moe while the raw pause point text stays side by side), scope fallback (verified with a synthetic scoped theme), and css-source assertions (moe emoji mappings, the six themes' background motifs).

Overall baseline: 24 frontend test files green (Executive Summary §8), of which the theme system accounts for 2. The `classic` extraction achieved zero behavior change (values copied verbatim from `tokens.css`); swatch colors come straight from the real css (moe = `#fff5f7`/`#5c3d47`/`#c2245c`, `smoke-theme.test.mjs:150`).

**Ripple effects**: the moe proposal demoted itself from "a second page" to one member of the theme catalog (`DEBUG-UI-MOE.md` header, v0.2); the copy contract outgrew the debug console — Skill Lab and the escalation inbox also read from the copy tables (`lab.*`, `escalation.*` keys), making the theme system the single outlet for UI copy app-wide; and once `sprite8` proved the mascot abstraction replaceable, the criterion "a theme is a temperament package; the mascot is just an optional asset" (`DEBUG-UI-THEMES.md:61`) held. Phase T4 had planned page-by-page vetting before opening `scope`; in practice T1.1 went app-wide immediately — justified precisely by zero-branch components plus full-contract validation making "a missing piece" structurally impossible (`DEBUG-UI-THEMES.md:146-149`).

## 6. Limitations & Boundaries

1. **Motion playback layer not implemented**: the motion field is currently registration data only (all `subtle`); the five named contract motions have no runtime; contract test #5 (asserting `reduced-motion` forces `instant`) is absent — degradation today relies solely on per-theme `prefers-reduced-motion` css fallbacks.
2. **Contrast testing covers only static token pairs**: the test computes "text color × base variable" pairs (`themes-contract.test.mjs:77`); it does not cover the actual perceived result of body background patterns (sakura / grids / scanlines) composited under panels — pattern opacity relies on author restraint and is outside the gate.
3. **The technical-text exemption is a convention, not enforced**: tests assert key coverage and a few side-by-side forms (e.g. `(aborted)`), but no static scan stops an author from stuffing raw error text into the copy table; enforcement ultimately rests on review.
4. **Mascot expressiveness is far below the design**: most of the nine-row state table in `DEBUG-UI-MOE.md` §2 (eye-rubbing, chin-on-paw bubbles, note-slipping interventions, bowing out) is unimplemented; today there are four expressions plus a reused `ready`, and states like aborted are unmapped — the layer disappears entirely (`mascot.js:18`), leaving gaps in mascot state expression.
5. **Themes are code contributions**: no user-facing theme editor (`DEBUG-UI-THEMES.md:153`), and variants do not multiply (terminal's amber was the only designed `--variant` example; v1 did not build it — see the note at `terminal.css:6`). Personalization costs a css file and a contribution workflow.
6. **The scope-fallback mechanism has never seen real use**: all six themes are `app-wide`; the fallback path is covered only by a synthetic-theme test (`smoke-theme.test.mjs:331-355`). The mechanism is reserved for "future half-finished themes" and has yet to intercept a real one.
7. **The two validation environments can drift**: browser registration reads `styleSheets`, while node tests parse css source — two independent code paths that share the contract list but not the parser; edge cases (e.g. selector-syntax differences) could pass one side and fail the other.
8. **No sound system**: pixel leaves a hook but defaults to mute (`DEBUG-UI-THEMES.md:154`); game-like feedback such as `LEVEL CLEAR!` has a visual channel only.

## 7. References

- Design docs: `docs/DEBUG-UI-THEMES.md` (theme-system plan: contract / catalog / phasing / non-goals), `docs/DEBUG-UI-MOE.md` (moe single-theme spec), `docs/WEB-UI.md` (design baseline = the classic theme)
- Source: `agent_os/src/agent_os/host/web/static/js/themes.js` (contract lists / registry / switching layer), `.../js/components/mascot.js` (MascotLayer and the MASCOTS registry), `.../css/themes/{classic,moe,terminal,blueprint,ink,pixel}.css`, `.../js/copy/{classic,moe,terminal,blueprint,ink,pixel}.js`
- Tests: `agent_os/src/agent_os/host/web/static/tests/themes-contract.test.mjs` (five contract assertion groups), `.../tests/smoke-theme.test.mjs` (12 runtime scenario groups)
- Related chapter: Chapter 00 §6.3 (positioning in the executive summary)

> Source discrepancies (code wins): ① the initial palette in `DEBUG-UI-MOE.md` §3 (cream base `#fdf6f0`, `--ok: #5ec9a7`, etc.) was replaced by the T1.2 sakura rework — `moe.css:13` notes "the original beige cream `#fdf6f0` family is retired"; actual values are `--bg-0: #fff5f7`, `--ok: #1b7355` (luminance lowered to keep 4.5:1). ② The prose list in `DEBUG-UI-THEMES.md` §2.1 omits `--line-strong`/`--text-2xs`/`--s1-5`/`--r-conn`, while `CONTRACT_TOKENS` (`themes.js:25-40`) includes them — the code list is authoritative: 43 variables.
