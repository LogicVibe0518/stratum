// What this post has cost in model calls, in the top bar: one number, and on a click
// where it went. Changes made by hand cost nothing, which is worth saying out loud.

import * as Popover from "@radix-ui/react-popover";
import { CircleDollarSign } from "lucide-react";
import type { PostCost } from "../lib/types";

/** "$0.0021" under a cent, "$0.42" above. */
export function money(usd: number): string {
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function CostChip({ cost }: { cost: PostCost }) {
  const perChange = cost.paid_changes ? cost.changes_usd / cost.paid_changes : 0;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          className="cost-chip"
          aria-label={`AI cost of this post: ${money(cost.total_usd)}`}
        >
          <CircleDollarSign size={14} aria-hidden /> {money(cost.total_usd)}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="float cost-pop" sideOffset={8} align="end">
          <h3>What this post has cost</h3>
          <dl>
            <dt>Writing and laying it out</dt>
            <dd>{money(cost.draft_usd)}</dd>
            <dt>{plural(cost.paid_changes, "plain-English change", "plain-English changes")}</dt>
            <dd>{money(cost.changes_usd)}</dd>
            <dt>{plural(cost.free_changes, "change by hand", "changes by hand")}</dt>
            <dd>free</dd>
            <dt className="total">Total</dt>
            <dd className="total">{money(cost.total_usd)}</dd>
          </dl>
          <p className="muted small">
            {perChange > 0 ? `About ${money(perChange)} per plain-English change. ` : ""}
            Dragging, typing on the poster and the instant buttons use no model. The agency’s AI allowance is{" "}
            {money(cost.daily_cap_usd)} a day.
          </p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
