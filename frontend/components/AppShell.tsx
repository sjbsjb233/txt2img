import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex">
      <Sidebar />
      <main className="flex-1 min-w-0">
        <div className="px-6 md:px-10 py-8 max-w-[1400px] mx-auto">{children}</div>
      </main>
    </div>
  );
}
