import {
  forwardRef,
  type ReactNode,
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
  type SelectHTMLAttributes,
} from 'react';

interface FormFieldProps {
  label: ReactNode;
  /** Renders a red asterisk after the label. */
  required?: boolean;
  /** Muted inline hint after the label, e.g. "(optional)". */
  hint?: ReactNode;
  /** Helper text rendered below the control. */
  helper?: ReactNode;
  /** Error text rendered below the control. */
  error?: string;
  /** Extra classes on the field wrapper (e.g. flex-1 for a growing textarea). */
  className?: string;
  children: ReactNode;
}

/** Label + control + helper/error wrapper. */
export function FormField({ label, required, hint, helper, error, className, children }: FormFieldProps) {
  return (
    <div className={className}>
      <label className="block text-sm font-medium text-[var(--muted)] mb-1">
        {label}
        {required && <span className="text-[var(--accent)]"> *</span>}
        {hint && <span className="text-[10px] font-normal ml-1">{hint}</span>}
      </label>
      {children}
      {helper && <div className="text-xs text-[var(--muted)] mt-1">{helper}</div>}
      {error && <div className="text-sm text-[var(--accent)] mt-1 px-1">{error}</div>}
    </div>
  );
}

export const TextField = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextField({ className, ...rest }, ref) {
    return <input ref={ref} className={className ? `form-input ${className}` : 'form-input'} {...rest} />;
  }
);

export const TextArea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function TextArea({ className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={className ? `form-input resize-none ${className}` : 'form-input resize-none'}
        {...rest}
      />
    );
  }
);

export function SelectField({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={className ? `form-input ${className}` : 'form-input'} {...rest}>
      {children}
    </select>
  );
}
