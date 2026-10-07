<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Fern Custom Components

Components registered through `experimental.mdx-components: ./components` in
[`docs.yml`](../docs.yml). This file holds their page-usage examples.

## Why The Examples Live Here And Not In The Components

Fern's bundler scans every component source for import specifiers and treats
anything that is neither relative nor in its allowlist (`react`, `react-dom`,
`@mdx-js/react`, `next`) as a third-party dependency, then shells out to
`npx rolldown` to bundle it — a registry download on every docs build.

The scan is a regular expression over raw text. It does not skip comments. A
usage example written in a docblock is therefore indistinguishable from a real
dependency, and three of them were enough to make docs previews fail
intermittently with `rolldown exited with code 127` and
`ERR_MODULE_NOT_FOUND`.

The scan only looks at `.js`, `.jsx`, `.ts` and `.tsx`. Markdown is invisible to
it, so examples here can use ordinary syntax and stay copy-pasteable.
`../scripts/check_component_imports.py` enforces that no component source
carries a quoted non-relative specifier, in code or in a comment.

## Page Usage

Import the component, then place it in the page body. Ambient use without an
import renders `Unsupported JSX tag`. The `@/` prefix resolves to the `fern/`
root and is rewritten to a relative path at publish time.

### DiffusionCatalog

The diffusion overview's searchable, filterable card grid. Keep card markup, provider images, and
the single native sidebar-page link per card in MDX so Fern can rewrite them. The component injects
scoped styles on the server, including dark mode, keyboard focus, and reduced-motion support. Its
neutral panels, fields, and pill chips mirror the Reference palette (`ReferenceStyles`, the
`CompatibilityHero` select, and the `.dynamo-chip-*` tints in `main.css`). Each card's type chip,
at the top right, carries its modality color, backend chips carry per-backend tints, experimental
entries use the dashed-amber badge, and hover and focus states use green.
`DiffusionCatalogControls` reads the rendered card metadata and visible labels
for type, backend, provider, weight size, and experimental-status filters. Search includes card
text, model IDs, and source paths; sorting supports names, labels, and numeric weight sizes. It
reorders the native cards so keyboard and screen-reader order follow the visual order. All cards
remain readable without JavaScript, and no model metadata is fetched in the browser.

```mdx
import { DiffusionCatalog } from "@/components/DiffusionCatalog";

<DiffusionCatalog>
  {/* Native card markup; see pages/recipes/examples/diffusion-overview.mdx. */}
</DiffusionCatalog>
```

### RecipeStyles

Once per recipe/benchmark page, and on the two landing READMEs, immediately
after the frontmatter.

```mdx
import { RecipeStyles } from "@/components/RecipeStyles";

<RecipeStyles />
```

### ReferenceStyles

Once per Reference page that uses the Reference components, immediately after
the frontmatter.

```mdx
import { ReferenceStyles } from "@/components/ReferenceStyles";

<ReferenceStyles />
```

### LandingStyles

Once on `welcome.mdx` and `community/README.mdx`, immediately after the
imports.

```mdx
import { LandingStyles } from "@/components/LandingStyles";

<LandingStyles />
```

### BlogStyles

Once on the digest landing page and every article page, immediately after the
imports.

```mdx
import { BlogStyles } from "@/components/BlogStyles";

<BlogStyles />
```

### TerminalDemo

Props are documented in the component's own header.

`src` must be a path relative to the page, because that is the only form Fern
rewrites to the published asset URL. A site-absolute path starting from the
docs root is rewritten by nothing and reaches the browser verbatim, where it
404s — the same shape as the regression that blanked the Home hero mark in
#12373. `scripts/check_asset_paths.py` rejects that form, and this file is now
inside its scan, so the example below is enforced rather than merely stated.

```mdx
import { TerminalDemo } from "@/components/TerminalDemo";

<TerminalDemo
  src="../../assets/hero-demo-25.cast"
  startAt={0}
  endAt={18}
  idleTimeLimit={2}
  speed={1.2}
/>
```

## Adding A Component

- No third-party dependencies. There is no `package.json` alongside `fern/`, so
  anything outside Fern's allowlist has to be loaded at runtime instead — see
  `TerminalDemo.tsx`, which pulls asciinema-player from a CDN for this reason.
- Put the usage example in this file, not in the component's docblock.
- CSS bundles that must survive the production theme go in a page-level
  `<style>` block. Use `dangerouslySetInnerHTML` when the CSS contains `>`
  child combinators or `&`, as `RecipeStyles`, `LandingStyles` and `BlogStyles`
  do.
- Keep backticks out of any string inside a CSS template literal, comments
  included. A raw backtick closes the literal and the file stops compiling.

### ExamplesCatalog

The main Examples overview owns the catalog metadata and its version-aware card links.
Subsection overview pages mirror only their topic's cards, without the topic label, and pass
`topicFilter={false}` to drop the single-option Topic filter; tests enforce parity. The component
adds search and topic/platform/backend filters to either page shape. Its styles match
`DiffusionCatalog`: each topic tints the card's icon tile, topic label, arrow, and glow, backend
chips reuse the diffusion catalog's tints, and hover and focus states use green. `ExampleTargets`
renders one chip per backend from the card's `platform:backend` pairs, with a Kubernetes or
Local / CLI glyph for each platform; the results row shows a key for the glyphs.

```mdx
import { ExamplesCatalog } from "@/components/ExamplesCatalog";
import { ExampleTargets } from "@/components/ExampleTargets";

<ExamplesCatalog>

<div className="dynamo-example-card" data-example="aggregated" data-topic="basic-serving" data-topic-label="Basic Serving" data-targets="kubernetes:vllm local:vllm" data-keywords="agg.sh agg.yaml">

<span className="dynamo-example-icon"><Icon icon="cube" /></span>

<p className="dynamo-example-topic">Basic Serving</p>

### Aggregated Serving

Frontend and aggregated workers.

<ExampleTargets targets="kubernetes:vllm local:vllm" />

<a className="dynamo-example-card-link" href="aggregated.mdx">Open Aggregated Serving example</a>

</div>

</ExamplesCatalog>
```

See [Authoring Examples](../pages/recipes/examples/_catalog/README.md) for the card contract.

### ExampleSelector

Use once on a deployment example page. Each option names a DGD/DGDR or launch-script source bundle; the component
selects the platform, backend, and (when there are multiple choices) the example in one panel.
It reuses the local installation selector's styles and leaves the selected source blocks open.

```mdx
import { ExampleSelector } from "@/components/ExampleSelector";

<ExampleSelector
  variants={[
    {"id": "kubernetes-vllm-agg", "target": "kubernetes:vllm", "label": "agg.yaml", "description": "Aggregated serving"}
  ]}
>

<div className="dynamo-example-variant" data-example-variant="kubernetes-vllm-agg">

<Code src="../../../../../examples/backends/vllm/deploy/agg.yaml" title="agg.yaml" language="yaml" maxLines={0} />

</div>

</ExampleSelector>
```

Keep source blocks and links in MDX, rather than passing their contents through component props.
The first valid target is open in the server-rendered HTML; changing a row switches the source
bundle in place. Unavailable backend/platform combinations cannot be selected.

Only deployment manifests and launch scripts belong in the picker. Supporting files stay out of the
selector and its code blocks for now. Singleton rows use the selected button style, not plain text;
clicking an already-selected axis does not reset the example choice.
