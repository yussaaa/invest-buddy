import * as React from "react"

import { cn } from "@/lib/utils"

const Tooltip: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
  className,
  children,
  ...props
}) => (
  <div className={cn("group relative inline-flex", className)} {...props}>
    {children}
  </div>
)
Tooltip.displayName = "Tooltip"

const TooltipTrigger = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => (
  <div ref={ref} className={cn("inline-flex", className)} {...props}>
    {children}
  </div>
))
TooltipTrigger.displayName = "TooltipTrigger"

interface TooltipContentProps extends React.HTMLAttributes<HTMLDivElement> {
  side?: "top" | "bottom" | "left" | "right"
  sideOffset?: number
}

const TooltipContent = React.forwardRef<HTMLDivElement, TooltipContentProps>(
  ({ className, side = "top", sideOffset = 4, children, ...props }, ref) => {
    const positionClasses = {
      top: `bottom-full left-1/2 -translate-x-1/2 mb-${sideOffset}`,
      bottom: `top-full left-1/2 -translate-x-1/2 mt-${sideOffset}`,
      left: `right-full top-1/2 -translate-y-1/2 mr-${sideOffset}`,
      right: `left-full top-1/2 -translate-y-1/2 ml-${sideOffset}`,
    }

    return (
      <div
        ref={ref}
        role="tooltip"
        className={cn(
          "pointer-events-none absolute z-50 hidden overflow-hidden rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md group-hover:block",
          "animate-in fade-in-0 zoom-in-95",
          positionClasses[side],
          className
        )}
        {...props}
      >
        {children}
      </div>
    )
  }
)
TooltipContent.displayName = "TooltipContent"

// No-op provider for API compatibility with Radix-based tooltips
const TooltipProvider: React.FC<{ children: React.ReactNode; delayDuration?: number }> = ({
  children,
}) => <>{children}</>
TooltipProvider.displayName = "TooltipProvider"

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider }
