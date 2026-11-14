// The dashboard's pages. Each layer owns its folder (src/plugin, src/memory) like the Python side;
// a new module adds its pages here, and its API routes in its dashboard() spec (plugin/dashboard.py).
import type { LucideIcon } from "lucide-react";
import type { ComponentType } from "react";
import { memoryPanels } from "./memory";
import { pluginPanels } from "./plugin";

export type Panel = {
  id: string;
  title: string;
  icon: LucideIcon;
  order: number;
  /** A count shown next to the title in the sidebar (e.g. memories waiting for review). */
  badge?: () => Promise<number>;
  Page: ComponentType<{ arg: string }>;
};

export const panels: Panel[] = [...pluginPanels, ...memoryPanels].sort((a, b) => a.order - b.order);
