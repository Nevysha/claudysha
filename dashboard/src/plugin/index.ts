import { House, WandSparkles } from "lucide-react";
import type { Panel } from "@/panels";
import { Overview } from "./overview";
import { SessionPreview } from "./session-preview";

export const pluginPanels: Panel[] = [
  { id: "overview", title: "Overview", icon: House, order: 0, Page: Overview },
  { id: "session", title: "Session preview", icon: WandSparkles, order: 30, Page: SessionPreview },
];
