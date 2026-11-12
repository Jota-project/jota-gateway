# Changes to CSS Color Parser

### 4.2.7

_October 29, 2026_

- Fix handling of out of range percentages from math functions in `color-mix()` (they are clamped, not invalid)
- Fix powerless hue detection for `hwb()` colors with negative whiteness or blackness (these values are not clamped)

[Full CHANGELOG](https://github.com/csstools/postcss-plugins/tree/main/packages/css-color-parser/CHANGELOG.md)
