interface InfoLabelProps {
  label: string;
  hint: string;
  htmlFor?: string;
  className?: string;
}

/** Field label with a dotted underline that reveals `hint` in a tooltip on hover. */
export function InfoLabel({ label, hint, htmlFor, className }: InfoLabelProps) {
  return (
    <label
      htmlFor={htmlFor}
      className={`relative group inline-flex w-fit cursor-help ${className || 'block text-xs font-semibold text-zinc-400 mb-1.5'}`}
    >
      <span className="border-b border-dotted border-zinc-600 group-hover:border-zinc-400 transition-colors">
        {label}
      </span>

      <div className="absolute left-0 bottom-full mb-2 w-64 p-2.5 bg-zinc-950/95 backdrop-blur-md border border-zinc-800 rounded-xl shadow-2xl opacity-0 scale-95 pointer-events-none group-hover:opacity-100 group-hover:scale-100 transition-all duration-200 z-50 transform translate-y-1 group-hover:translate-y-0">
        <p className="text-[11px] leading-relaxed text-zinc-300 font-normal normal-case tracking-normal">
          {hint}
        </p>
      </div>
    </label>
  );
}
