// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useRef, type ComponentProps, type ReactNode } from "react";
import { Checkbox as CheckControl, Dialog as DialogControl, Label as LabelControl,
  Progress as ProgressControl, Slot, Switch as SwitchControl, Tabs as TabsControl,
  Tooltip as TooltipControl } from "radix-ui";
import { Check, ChevronDown, ChevronUp, Minus, X } from "lucide-react";

type ButtonProps = ComponentProps<"button"> & {
  asChild?: boolean;
  variant?: "default" | "outline" | "ghost" | "secondary" | "destructive" | "link" | "dark";
  size?: "default" | "sm" | "lg" | "icon" | "icon-sm" | "icon-xs" | "icon-lg" | "xs";
};
export function Button({ asChild, variant = "default", size = "default", className = "", ...props }: ButtonProps) {
  const Element = asChild ? Slot.Root : "button";
  return <Element data-slot="button" data-variant={variant} data-size={size}
    className={`oc-kit-button ${className}`} {...props} />;
}
export function Input({ className = "", ref, ...props }: ComponentProps<"input">) {
  const element = useRef<HTMLInputElement | null>(null);
  const input = <input data-slot="input" className={`oc-kit-input ${className}`} {...props} ref={node => {
    element.current = node;
    if (typeof ref === "function") return ref(node);
    if (ref) ref.current = node;
  }} />;
  if (props.type !== "number") return input;
  const step = (direction: number) => {
    const node = element.current;
    if (!node || node.disabled || node.readOnly) return;
    const amount = props.step === "any" ? 1 : Number(props.step ?? 1);
    const current = Number(node.value) || 0;
    const next = Math.max(Number(props.min ?? -Infinity), Math.min(Number(props.max ?? Infinity),
      Number((current + direction * amount).toPrecision(12))));
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(node, String(next));
    node.dispatchEvent(new Event("input", { bubbles: true }));
  };
  return <div className="oc-kit-number">{input}<div className="oc-kit-stepper">
    {[1, -1].map(direction => <button key={direction} type="button" tabIndex={-1} aria-label={direction > 0 ? "Increase value" : "Decrease value"}
      disabled={props.disabled || props.readOnly} onPointerDown={event => event.preventDefault()} onClick={() => step(direction)}>
      {direction > 0 ? <ChevronUp /> : <ChevronDown />}
    </button>)}
  </div></div>;
}
export function Textarea({ className = "", ...props }: ComponentProps<"textarea">) {
  return <textarea data-slot="textarea" className={`oc-kit-textarea ${className}`} {...props} />;
}
export function Label({ className = "", ...props }: ComponentProps<typeof LabelControl.Root>) {
  return <LabelControl.Root data-slot="label" className={`oc-kit-label ${className}`} {...props} />;
}
export function Switch({ className = "", ...props }: ComponentProps<typeof SwitchControl.Root>) {
  return <SwitchControl.Root data-slot="switch" className={`oc-kit-switch ${className}`} {...props}>
    <SwitchControl.Thumb data-slot="switch-thumb" />
  </SwitchControl.Root>;
}
export function Checkbox({ className = "", checked, ...props }: ComponentProps<typeof CheckControl.Root>) {
  return <CheckControl.Root data-slot="checkbox" className={`oc-kit-checkbox ${className}`} checked={checked} {...props}>
    <CheckControl.Indicator>{checked === "indeterminate" ? <Minus /> : <Check />}</CheckControl.Indicator>
  </CheckControl.Root>;
}
export function Progress({ value, className = "", ...props }: ComponentProps<typeof ProgressControl.Root>) {
  const percent = value == null ? null : Math.max(0, Math.min(100, value));
  return <ProgressControl.Root data-slot="progress" className={`oc-kit-progress ${className}`} value={percent} {...props}>
    <ProgressControl.Indicator data-slot="progress-indicator"
      style={{ transform: `translateX(-${100 - (percent ?? 0)}%)` }} />
  </ProgressControl.Root>;
}
export function Tabs({ className = "", ...props }: ComponentProps<typeof TabsControl.Root>) {
  return <TabsControl.Root data-slot="tabs" className={`oc-kit-tabs ${className}`} {...props} />;
}
export function TabsList({ className = "", ...props }: ComponentProps<typeof TabsControl.List>) {
  return <TabsControl.List data-slot="tabs-list" className={`oc-kit-tab-list ${className}`} {...props} />;
}
export function TabsTrigger({ className = "", ...props }: ComponentProps<typeof TabsControl.Trigger>) {
  return <TabsControl.Trigger data-slot="tabs-trigger" className={`oc-kit-tab ${className}`} {...props} />;
}
export function TabsContent({ className = "", ...props }: ComponentProps<typeof TabsControl.Content>) {
  return <TabsControl.Content data-slot="tabs-content" className={`oc-kit-tab-panel ${className}`} {...props} />;
}

export const Dialog = DialogControl.Root;
export function DialogContent({ className = "", children, ...props }: ComponentProps<typeof DialogControl.Content>) {
  return <DialogControl.Portal>
    <DialogControl.Overlay data-slot="dialog-overlay" className="oc-kit-overlay" />
    <DialogControl.Content data-slot="dialog-content" className={`oc-kit-dialog ${className}`} {...props}>
      {children}
      <DialogControl.Close asChild>
        <Button variant="ghost" size="icon-sm" data-slot="dialog-close" className="oc-kit-dialog-close">
          <X /><span className="sr-only">Close</span>
        </Button>
      </DialogControl.Close>
    </DialogControl.Content>
  </DialogControl.Portal>;
}
export function DialogHeader({ className = "", ...props }: ComponentProps<"div">) {
  return <div data-slot="dialog-header" className={`oc-kit-dialog-header ${className}`} {...props} />;
}
export function DialogTitle({ className = "", ...props }: ComponentProps<typeof DialogControl.Title>) {
  return <DialogControl.Title data-slot="dialog-title" className={`oc-kit-dialog-title ${className}`} {...props} />;
}
export function DialogDescription({ className = "", ...props }: ComponentProps<typeof DialogControl.Description>) {
  return <DialogControl.Description data-slot="dialog-description" className={`oc-kit-dialog-description ${className}`} {...props} />;
}
export function TooltipProvider(props: ComponentProps<typeof TooltipControl.Provider>) {
  return <TooltipControl.Provider delayDuration={400} {...props} />;
}
export const Tooltip = TooltipControl.Root;
export function TooltipTrigger(props: ComponentProps<typeof TooltipControl.Trigger>) {
  return <TooltipControl.Trigger data-slot="tooltip-trigger" {...props} />;
}
export function TooltipContent({ className = "", children, ...props }: ComponentProps<typeof TooltipControl.Content>) {
  return <TooltipControl.Portal>
    <TooltipControl.Content data-slot="tooltip-content" sideOffset={6} className={`oc-kit-tooltip ${className}`} {...props}>
      {children}<TooltipControl.Arrow className="oc-kit-tooltip-arrow" />
    </TooltipControl.Content>
  </TooltipControl.Portal>;
}
export function TooltipIconButton({ tooltip, children, ...props }: ButtonProps & { tooltip: ReactNode }) {
  return <Tooltip>
    <TooltipTrigger asChild><Button variant="ghost" size="icon-xs" className="oc-kit-icon-action" {...props}>
      {children}<span className="sr-only">{tooltip}</span>
    </Button></TooltipTrigger>
    <TooltipContent>{tooltip}</TooltipContent>
  </Tooltip>;
}
