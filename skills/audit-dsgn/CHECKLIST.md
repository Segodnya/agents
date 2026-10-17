# Developer audit checklist

Condensed from the article «Аудит дизайна» (PDF). The auditor's core question: can I, or any frontend developer, build this with what the mockup gives, without raising entropy in the system?

The items marked *(advisory)* are worth raising, but they are not grounds for «АУДИТ НЕ ОК».

## 1. Performance

- How does the new element load into the page: sync or async? Bundle size must not grow dramatically. New styles for existing controls bloat the CSS bundle.
- Images: SVG preferred. Retina support required, and screenshots must come from a retina screen. After compression: ≤100 KB at 1x, ≤300 KB at 2x.

## 2. Flexibility and scale

- Easy to extend with new features.
- Content cases: a lot of content, little content, no content (empty state). Long names count as "a lot".

## 3. States

- Controls: disabled, readonly, keyboard-selected (arrowing through a select), hover, focus, pressed, loading.
- Every async operation has a loader, and the UI never feels broken.
- Errors: validation, request failure and limits, each clear and informative.
- Smooth, unobtrusive transitions.
- Rights: admin vs non-admin, and fine-grained rights that hide an entity.
- Tariffs: every plan, including trial, downgrade, and feature flags off.

## 4. Consistency

- Uses existing UI-kit components and the layouts the system already uses. Spacing matches, and no new styles force hacks.
- Top padding is never larger than bottom padding.
- Adaptive: CRM from 965px wide; promo from 320px.

## 5. UX

- Keyboard interaction is supported.
- Convenience, intuitive labels (button text describes the action), minimal user actions *(advisory)*.

## 6. Logic

- Every business case from the ticket is covered on the screens. Look for over-complication that could be simplified *(advisory)*.
- The design matches business requirements and the app's logic.
