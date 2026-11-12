# Product

## Register

product

## Users

Indie developers and solo founders who scan GitHub Trending for projects worth
replicating. They use RepoScout in a short daily session (about 10 minutes),
usually on a laptop in low light, with the goal of turning a noisy trending list
into a small, defensible shortlist. They care about growth, build effort,
license risk, and whether a commercial gap still exists.

## Product Purpose

RepoScout turns raw GitHub signals into an evidence-backed decision surface:
which repositories are growing, how hard they are to rebuild, and whether there
is a credible commercial opportunity. Success is a user acting on one or two
well-justified candidates per day, not scrolling a longer list.

## Brand Personality

Calm, precise, quietly tactical. The interface should feel like an intelligence
desk: dense where it matters, sparse everywhere else, never theatrical. Numbers
are monospaced and aligned; evidence is always one step away.

## Anti-references

- Generic SaaS template: gradient headings, glassmorphism, identical card grids,
  oversized metric + tiny label patterns.
- Black-and-neon-green hacker terminal aesthetics.
- Decorative motion, scroll reveals, or animation that delays reading.
- Color-only meaning (especially for license and risk).
- Nested cards used to fake hierarchy.

## Design Principles

1. **Decisions over decoration.** Every visible element should help rank, filter,
   or justify a candidate.
2. **Evidence stays attached.** Scores are never shown without their inputs,
   sources, or confidence close at hand.
3. **Progressive disclosure.** The table answers "what should I look at?"; detail
   pages answer "why, and what could go wrong?".
4. **Color is signal, not mood.** Moss green marks the primary path; amber and red
   are reserved for risk and license warnings.
5. **Accessible by default.** Keyboard paths, AA contrast, reduced-motion support,
   and text labels alongside every color code.

## Accessibility & Inclusion

Target WCAG 2.2 AA. Body text at least 4.5:1 contrast, visible focus rings,
full keyboard operation, semantic table markup, and `prefers-reduced-motion`
fallbacks. License/risk meaning must be readable without color. Chinese-first UI
with English identifiers; number formatting uses thousands separators.
