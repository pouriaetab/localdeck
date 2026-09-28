import { useEffect, useRef } from "react";
import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";

function safeFit(fitAddon) {
  try {
    fitAddon.fit();
  } catch (error) {
    /* container not measurable yet; ignore */
  }
}

export default function TerminalView({ logs, logSeq }) {
  const rootRef = useRef(null);
  const terminalRef = useRef(null);
  const fitAddonRef = useRef(null);
  const writtenSeqRef = useRef(0);

  useEffect(() => {
    if (!rootRef.current) {
      return undefined;
    }

    const terminal = new Terminal({
      convertEol: true,
      fontFamily: "'IBM Plex Mono', Menlo, Monaco, monospace",
      fontSize: 12.5,
      lineHeight: 1.25,
      theme: {
        background: "#1b1b19",
        foreground: "#e8e6df",
        cursor: "#c96442",
        selectionBackground: "rgba(201, 100, 66, 0.3)",
      },
      scrollback: 5000,
      disableStdin: true,
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(rootRef.current);
    safeFit(fitAddon);

    const observer = new ResizeObserver(() => safeFit(fitAddon));
    observer.observe(rootRef.current);

    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;
    writtenSeqRef.current = 0;

    return () => {
      observer.disconnect();
      terminal.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, []);

  // Append only the new lines so output streams smoothly, line by line.
  //
  // What is "new" is decided by `logSeq` — the number of lines the project has
  // printed since its last "Clear logs" — never by how many lines the buffer
  // holds. The buffer is a 2000-line ring: once a long-running project fills it,
  // its length stays at 2000 forever, so a length comparison sees "nothing new"
  // on every subsequent line and the terminal freezes on the 2000th line while
  // the project keeps running. `logSeq` keeps counting past that.
  useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) {
      return;
    }

    const lines = logs ?? [];
    const seq = typeof logSeq === "number" ? logSeq : lines.length;
    const pending = seq - writtenSeqRef.current;

    if (pending === 0) {
      return; // a re-render with the same output; nothing to draw
    }

    // The count went backwards: logs were cleared, or this panel is now showing
    // a different run. Start the terminal over from the current buffer.
    if (pending < 0) {
      terminal.reset();
      writtenSeqRef.current = seq;
      if (lines.length > 0) {
        terminal.write(lines.join("\r\n") + "\r\n");
        terminal.scrollToBottom();
      }
      return;
    }

    // Write as many of the new lines as the buffer still holds. If more than
    // 2000 arrived since the last render the oldest of them have already been
    // dropped by the backend, so the terminal shows the same tail everything
    // else does rather than silently stalling.
    const fresh = lines.slice(Math.max(0, lines.length - pending));
    writtenSeqRef.current = seq;
    if (fresh.length > 0) {
      terminal.write(fresh.join("\r\n") + "\r\n");
      terminal.scrollToBottom();
    }
  }, [logs, logSeq]);

  return <div className="terminal-shell" ref={rootRef} />;
}
