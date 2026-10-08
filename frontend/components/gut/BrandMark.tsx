export default function BrandMark({ className = "h-9 w-9" }: { className?: string }) {
  return <span className={`gut-brand ${className}`} aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
    <path className="gut-ring" d="M21 12a9 9 0 1 1-9-9" stroke="#FFFFFF" />
    <path className="gut-arrow" d="M7.5 15l3.5-3.5 2.5 2L20 4M15.5 4H20v4.5" stroke="#C6F432" />
  </svg></span>;
}
