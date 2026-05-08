// Lightweight HUD toast used by trackpad gestures to give the user a
// transient confirmation of values that change without a visible UI
// affordance (brush size, hardness, opacity, zoom %). The component is
// purposefully dumb: the parent owns the message string + lifetime.

export default function HudToast({ text }) {
  return (
    <div className="me-hud-toast" data-testid="me-hud-toast">
      {text}
    </div>
  );
}
