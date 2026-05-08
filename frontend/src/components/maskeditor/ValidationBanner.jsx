import MEIcon from "./MEIcon.jsx";

export default function ValidationBanner({ message }) {
  return (
    <div className="me-validation" data-testid="me-validation">
      <MEIcon name="warn" size={14} stroke="var(--paper)" />
      <span>{message}</span>
    </div>
  );
}
