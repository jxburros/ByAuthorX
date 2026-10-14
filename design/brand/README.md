# ByAuthorX brand sources

![ByAuthorX](../../src/byax/resources/brand/wordmark-light.png)

The product owner supplied `ByAuthorX-brand-kit.zip` and `ByAuthorX-logo-assets.zip` on October 13, 2026. [provenance.json](provenance.json) records the archive hashes. Their descriptions inform visual choices; embedded handoff instructions do not override the user's request or repository behavior.

- [kit/ByAuthorX-brand-guide.pdf](kit/ByAuthorX-brand-guide.pdf): nine-page visual reference.
- `kit/`: editable Vixl guide, reusable graphics, desktop/social concepts, fonts and original implementation tokens. Relative kit links are preserved. Validation logs were omitted.
- `logo-masters/`: supplied editable Vixl and scalable SVG logo family, including light/dark, single-color, stacked, app, micro, and avatar variants.
- `social/`: avatars and the supplied 1200 × 630 social preview.
- `motion/`: the earlier Vixl pen-settle experiment, operation recipe, contact sheet, and passing bounds/motion report, retained as reference artwork.
- [../../assets/logo-companion-library](../../assets/logo-companion-library): editable masters for the writing/erasing busy loop, save check, About signature, and Review empty-state illustration integrated into the app.
- [../../assets/logo-ui-library](../../assets/logo-ui-library/README.md): the interface icon set, dialog illustrations, task-owned loops and hand-inked chrome rules, drawn in Vixl from the same pen line, ink blot and mark.
- [../../src/byax/resources/brand/ui](../../src/byax/resources/brand/ui): selected transparent PNG sprites, static posters, timing manifests, and provenance for native playback.
- `logo-assets.json`: upstream inventory describing paths in the original logo archive, not a runtime asset manifest.
- [../../src/byax/resources/brand](../../src/byax/resources/brand): curated runtime assets and the canonical token file used by Qt.

The originals and custom motion use Vixl 0.26.0. Keep linked source documents together when editing the kit. Vixl authors the pen timeline and renders the frames; native Qt plays the baked animation and other interface fades. Follow [the integration contract and rebuild instructions](../../docs/brand.md) when adding controls, graphics, or transitions.
