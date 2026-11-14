import { Inbox, Search } from "lucide-react";
import type { Panel } from "@/panels";
import { getSummary } from "./api";
import { Memories } from "./memories";
import { Review } from "./review";

export const memoryPanels: Panel[] = [
  { id: "review", title: "Review", icon: Inbox, order: 10, badge: async () => (await getSummary()).counts.proposed, Page: Review },
  { id: "memories", title: "Memories", icon: Search, order: 20, Page: Memories },
];
