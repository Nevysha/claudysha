import { createContext, useContext } from "react";

/** Lets a page refresh the sidebar's counts after a change (e.g. a memory approved). */
export const NavContext = createContext({ refresh: () => {} });
export const useNav = () => useContext(NavContext);
