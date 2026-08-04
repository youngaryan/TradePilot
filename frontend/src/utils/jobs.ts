/**
 * Shared background-job polling.
 *
 * Every long-running workflow (backtest, paper run, research committee,
 * sentiment accumulation) uses this so retry cadence, cancellation, and the
 * bounded-attempt timeout behave identically everywhere.
 */

const TERMINAL_JOB_STATUSES = new Set(["completed", "failed", "interrupted"]);

export class JobPollingTimeoutError extends Error {
  constructor(attempts: number) {
    super(`Job did not finish after ${attempts} status checks. Retry or check the job history.`);
    this.name = "JobPollingTimeoutError";
  }
}

function pollingDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Polling cancelled", "AbortError"));
      return;
    }
    const cancel = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", cancel);
      reject(new DOMException("Polling cancelled", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", cancel, { once: true });
  });
}

export async function pollJobUntilTerminal<T extends { status: string }>(
  fetchJob: () => Promise<T>,
  options: {
    signal: AbortSignal;
    maxAttempts?: number;
    initialDelayMs?: number;
    maxDelayMs?: number;
  },
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? 40;
  let delayMs = options.initialDelayMs ?? 1200;
  const maxDelayMs = options.maxDelayMs ?? 5000;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await pollingDelay(delayMs, options.signal);
    const job = await fetchJob();
    if (TERMINAL_JOB_STATUSES.has(job.status)) return job;
    delayMs = Math.min(maxDelayMs, Math.ceil(delayMs * 1.35));
  }
  throw new JobPollingTimeoutError(maxAttempts);
}

export { TERMINAL_JOB_STATUSES };
