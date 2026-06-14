/**
 * useEventStream — React hook for live WebSocket events from cloudx.
 *
 * Connects to ws://host:port/ws/events and dispatches events.
 * Auto-reconnects on disconnection with exponential backoff.
 *
 * Usage:
 *   const { lastEvent, connected } = useEventStream();
 *
 *   useEffect(() => {
 *     if (lastEvent?.type === 'server.update') {
 *       refreshServerList();
 *     }
 *   }, [lastEvent]);
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws/events`;

export default function useEventStream() {
  const [lastEvent, setLastEvent] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const backoff = useRef(1000); // start with 1s

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        backoff.current = 1000; // reset backoff on successful connect
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setLastEvent(data);
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.onclose = () => {
        setConnected(false);
        // Reconnect with exponential backoff (max 30s)
        const delay = Math.min(backoff.current, 30000);
        backoff.current = delay * 1.5;
        reconnectTimer.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      // WebSocket constructor failed — retry
      const delay = Math.min(backoff.current, 30000);
      backoff.current = delay * 1.5;
      reconnectTimer.current = setTimeout(connect, delay);
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  return { lastEvent, connected };
}
