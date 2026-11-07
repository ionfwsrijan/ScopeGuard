import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold leading-none tracking-wide", {
  variants: {
    variant: {
      default: "border-primary/25 bg-primary/20 text-primary-foreground",
      secondary: "border-border bg-secondary text-secondary-foreground",
      outline: "border-border bg-transparent text-foreground",
      success: "border-emerald-200 bg-emerald-50 text-emerald-800",
      warning: "border-amber-200 bg-amber-50 text-amber-800",
      destructive: "border-rose-200 bg-rose-50 text-rose-800",
    },
  },
  defaultVariants: { variant: "secondary" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}
export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}
