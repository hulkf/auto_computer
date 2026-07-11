## Brand & Style
The design system is engineered for the high-stakes environment of local automation and self-healing infrastructure. The brand personality is **composed, reliable, and organic**, moving away from the cold, clinical feel of traditional DevOps tools toward a "living system" aesthetic. 

The visual style is a sophisticated blend of **Corporate Modern** and **Tactile Minimalism**. It utilizes a soft, nested-card architecture inspired by natural growth patterns. The UI should evoke a sense of calm under pressure, using high-contrast "signal colors" sparingly against a lush, forest-inspired palette to direct attention only where human intervention is required.

## Layout & Spacing
The layout follows a **Fixed-Fluid Hybrid** model optimized for 1440px desktop displays. 
- **The Sidebar** is fixed at 260px, serving as the primary navigation anchor.
- **The Dashboard** utilizes a 12-column grid within a contained 1180px workspace, centered on larger screens.
- **Content Blocks** use a 24px internal padding (container-padding) to ensure data-heavy elements don't feel cluttered.

The spacing rhythm is based on an 8px baseline. Use wider gaps (20px+) between major functional cards to allow the "shadow depth" to define the hierarchy effectively.

## Elevation & Depth
Depth is created through **Tonal Layering** and **Ambient Shadows**. 
- **Level 0 (Background):** Solid Mint Green (#D8F3DC) or Soft Gray.
- **Level 1 (Cards):** Pure White (#FFFFFF) surfaces with a subtle, diffuse shadow (0px 4px 20px rgba(27, 67, 50, 0.05)).
- **Level 2 (Modals/Popovers):** Pure White with a more pronounced shadow and a 1px border in a slightly darker mint tone to define boundaries.

Avoid heavy blacks in shadows; use a tinted Forest Green alpha for a softer, more integrated look.

## Components
### Buttons & Controls
- **Primary:** Forest Green background, White text. High-contrast, 12px corner radius.
- **Secondary:** Mint Green background, Forest Green text. Used for "Add" or "Import" actions.
- **Ghost:** Borderless with Forest Green text, used for navigation or less critical actions.

### Status Badges
Badges are pill-shaped with low-opacity backgrounds (15%) and high-opacity text (100%) of the same color family (e.g., Error: Red text on Light Red bg). For "Healing," use a subtle pulse animation on the icon.

### Cards & Tables
Cards are the primary structural unit. Tables inside cards should remove outer borders, using only light horizontal dividers. Header rows in tables should use the **label-caps** typography style.

### JSON Editor
The editor should be housed in a Level 1 card with a background of #1B4332 for "Dark Mode" code editing, ensuring high contrast for syntax highlighting in mint, white, and gold.

### Sidebar
The active state in the sidebar is indicated by a Forest Green icon and text, accompanied by a subtle left-aligned vertical bar.