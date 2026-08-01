/**
 * @module @eywalink/observability/metrics
 * Dependency-free metric primitives with Prometheus text exposition support.
 *
 * - Counter: monotonically increasing value
 * - Gauge:    settable value
 * - Histogram: value distribution with configurable buckets
 *
 * All metrics support optional label sets. The registry renders the standard
 * Prometheus text exposition format for /metrics endpoints.
 */

export type LabelValues = Record<string, string>;
export type MetricType = 'counter' | 'gauge' | 'histogram';

/** Canonical label sorting keeps serialized keys stable regardless of insertion order. */
export function serializeLabels(labels: LabelValues = {}): string {
  const keys = Object.keys(labels).sort();
  return keys.map((k) => `${k}="${labels[k]}"`).join(',');
}

export interface MetricFamily {
  name: string;
  help: string;
  type: MetricType;
  /** Prometheus text exposition block. */
  render(): string;
}

interface SampleSet {
  labels: LabelValues;
  labelKey: string;
}

class CounterImpl implements MetricFamily {
  readonly type = 'counter' as const;
  readonly name: string;
  readonly help: string;
  private samples = new Map<string, { labels: LabelValues; value: number }>();

  constructor(name: string, help: string) {
    this.name = name;
    this.help = help;
  }

  inc(labels: LabelValues = {}, value = 1): void {
    const key = serializeLabels(labels);
    const cur = this.samples.get(key) ?? { labels, value: 0 };
    cur.value += value;
    this.samples.set(key, cur);
  }

  get(labels: LabelValues = {}): number {
    return this.samples.get(serializeLabels(labels))?.value ?? 0;
  }

  render(): string {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} counter`];
    for (const { labels, value } of this.samples.values()) {
      const l = serializeLabels(labels);
      lines.push(`${this.name}{${l}} ${value}`);
    }
    return lines.join('\n');
  }
}

class GaugeImpl implements MetricFamily {
  readonly type = 'gauge' as const;
  readonly name: string;
  readonly help: string;
  private samples = new Map<string, { labels: LabelValues; value: number }>();

  constructor(name: string, help: string) {
    this.name = name;
    this.help = help;
  }

  set(value: number, labels: LabelValues = {}): void {
    this.samples.set(serializeLabels(labels), { labels, value });
  }

  add(delta: number, labels: LabelValues = {}): void {
    const key = serializeLabels(labels);
    const cur = this.samples.get(key) ?? { labels, value: 0 };
    cur.value += delta;
    this.samples.set(key, cur);
  }

  get(labels: LabelValues = {}): number {
    return this.samples.get(serializeLabels(labels))?.value ?? 0;
  }

  render(): string {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} gauge`];
    for (const { labels, value } of this.samples.values()) {
      const l = serializeLabels(labels);
      lines.push(`${this.name}{${l}} ${value}`);
    }
    return lines.join('\n');
  }
}

export const DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10];

class HistogramImpl implements MetricFamily {
  readonly type = 'histogram' as const;
  readonly name: string;
  readonly help: string;
  readonly buckets: number[];
  private samples = new Map<string, { labels: LabelValues; buckets: Map<number, number>; sum: number; count: number }>();

  constructor(name: string, help: string, buckets: number[] = DEFAULT_BUCKETS) {
    this.name = name;
    this.help = help;
    this.buckets = buckets;
  }

  observe(value: number, labels: LabelValues = {}): void {
    const key = serializeLabels(labels);
    const cur = this.samples.get(key) ?? {
      labels,
      buckets: new Map(this.buckets.map((b) => [b, 0])),
      sum: 0,
      count: 0,
    };
    cur.sum += value;
    cur.count += 1;
    for (const b of this.buckets) {
      if (value <= b) cur.buckets.set(b, (cur.buckets.get(b) ?? 0) + 1);
    }
    this.samples.set(key, cur);
  }

  get(labels: LabelValues = {}): { count: number; sum: number } {
    const s = this.samples.get(serializeLabels(labels));
    return { count: s?.count ?? 0, sum: s?.sum ?? 0 };
  }

  render(): string {
    const lines = [`# HELP ${this.name} ${this.help}`, `# TYPE ${this.name} histogram`];
    for (const { labels, buckets, sum, count } of this.samples.values()) {
      const l = serializeLabels(labels);
      const labelSuffix = l ? `{${l},le=` : `{le=`;
      // Buckets already hold cumulative counts (observe bumps every
      // threshold >= value), so render them as-is per Prometheus semantics.
      for (const b of this.buckets) {
        lines.push(`${this.name}_bucket${labelSuffix}"${b}"} ${buckets.get(b) ?? 0}`);
      }
      lines.push(`${this.name}_bucket${labelSuffix}"+Inf"} ${count}`);
      lines.push(`${this.name}_sum${l ? `{${l}}` : ''} ${sum}`);
      lines.push(`${this.name}_count${l ? `{${l}}` : ''} ${count}`);
    }
    return lines.join('\n');
  }
}

export class MetricRegistry {
  private families = new Map<string, MetricFamily>();

  counter(name: string, help: string): CounterImpl {
    return this.register(new CounterImpl(name, help));
  }

  gauge(name: string, help: string): GaugeImpl {
    return this.register(new GaugeImpl(name, help));
  }

  histogram(name: string, help: string, buckets?: number[]): HistogramImpl {
    return this.register(new HistogramImpl(name, help, buckets));
  }

  private register<T extends MetricFamily>(m: T): T {
    if (this.families.has(m.name)) {
      throw new Error(`Metric '${m.name}' already registered`);
    }
    this.families.set(m.name, m);
    return m;
  }

  /** Render all families in Prometheus text exposition format (LF-terminated blocks). */
  render(): string {
    return [...this.families.values()].map((f) => f.render()).join('\n') + '\n';
  }

  has(name: string): boolean {
    return this.families.has(name);
  }

  names(): string[] {
    return [...this.families.keys()];
  }
}
