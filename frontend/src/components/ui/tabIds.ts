/** Element ids linking a tab to its panel (components/ui/Tabs.tsx). */

export function tabId(group: string, value: string): string {
  return `${group}-tab-${value}`;
}

export function panelId(group: string, value: string): string {
  return `${group}-panel-${value}`;
}
