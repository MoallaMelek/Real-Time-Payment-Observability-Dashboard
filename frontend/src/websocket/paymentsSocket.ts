import type { PaymentSocketMessage, SocketStatus } from "../types/dashboard";

const WS_URL = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws/payments";
const WS_TOKEN = import.meta.env.VITE_WS_TOKEN;

interface SocketHandlers {
  onMessage: (message: PaymentSocketMessage) => void;
  onStatus: (status: SocketStatus) => void;
}

export function connectPaymentsSocket({ onMessage, onStatus }: SocketHandlers): () => void {
  let socket: WebSocket | null = null;
  let closedByClient = false;
  let retry = 0;
  let heartbeat: number | undefined;
  let heartbeatTimeout: number | undefined;
  let reconnectTimer: number | undefined;
  let openTimer: number | undefined;

  const open = () => {
    if (closedByClient) return;
    onStatus(retry === 0 ? "connecting" : "reconnecting");
    socket = new WebSocket(buildSocketUrl());

    socket.onopen = () => {
      retry = 0;
      onStatus("connected");
      heartbeat = window.setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "ping", sent_at: new Date().toISOString() }));
          window.clearTimeout(heartbeatTimeout);
          heartbeatTimeout = window.setTimeout(() => socket?.close(), 8000);
        }
      }, 15000);
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as PaymentSocketMessage;
        if (message.type === "pong") {
          window.clearTimeout(heartbeatTimeout);
          return;
        }
        if (message.type !== "error") {
          onMessage(message);
        }
      } catch {
        socket?.close();
      }
    };

    socket.onclose = () => {
      window.clearInterval(heartbeat);
      window.clearTimeout(heartbeatTimeout);
      if (closedByClient) {
        onStatus("closed");
        return;
      }
      retry += 1;
      onStatus("reconnecting");
      reconnectTimer = window.setTimeout(open, Math.min(6000, 600 * retry));
    };

    socket.onerror = () => {
      socket?.close();
    };
  };

  openTimer = window.setTimeout(open, 0);

  return () => {
    closedByClient = true;
    window.clearTimeout(openTimer);
    window.clearInterval(heartbeat);
    window.clearTimeout(heartbeatTimeout);
    window.clearTimeout(reconnectTimer);
    socket?.close();
  };
}

function buildSocketUrl(): string {
  if (!WS_TOKEN) return WS_URL;
  const url = new URL(WS_URL);
  url.searchParams.set("token", WS_TOKEN);
  return url.toString();
}
