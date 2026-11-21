import { ChatArea } from '../components/Chat/ChatArea';
import { SystemPanel } from '../components/Chat/SystemPanel';
import { useAppStore } from '../lib/store';

export function ChatPage() {
  const systemPanelOpen = useAppStore((s) => s.systemPanelOpen);
  const setSystemPanelOpen = useAppStore((s) => s.setSystemPanelOpen);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex-1 min-w-0">
        <ChatArea />
      </div>
      {systemPanelOpen && (
        <>
          {/* Phones: overlay the panel instead of squeezing the chat */}
          <div
            className="fixed inset-0 z-20 bg-black/40 md:hidden"
            onClick={() => setSystemPanelOpen(false)}
          />
          <div className="fixed inset-y-0 right-0 z-30 md:static md:z-auto">
            <SystemPanel />
          </div>
        </>
      )}
    </div>
  );
}
