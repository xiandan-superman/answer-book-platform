import { DurableObject } from "cloudflare:workers";

type Capability = "text" | "vision" | "image_generation" | "image_edit";
type ImageAdapter = "images" | "ark_seedream" | "dashscope" | "embedded_chat" | "embedded_responses";

interface RouteConfig {
  id: string;
  family: string;
  provider: string;
  provider_pool?: string;
  model: string;
  base_url: string;
  api_key_secret: string;
  api_protocol?: "chat_completions" | "responses";
  image_adapter?: ImageAdapter;
  image_size?: string;
  auth_header?: string;
  auth_scheme?: string;
  priority: number;
  provider_concurrency?: number;
  model_concurrency?: number;
  thinking_minimum?: string;
  capabilities: Capability[];
  enabled: boolean;
}

interface Reservation {
  id: string;
  user_id: string;
  family: string;
  capability: Capability;
  status: "waiting" | "ready" | "executing" | "finished";
  created_at: number;
  expires_at: number;
  route_id?: string;
  lease?: string;
  task_id?: string;
  stage?: string;
  active_item?: string;
}

interface Health {
  samples: number;
  successes: number;
  latency_total_ms: number;
  consecutive_failures: number;
  cooldown_until: number;
}

interface Env {
  ROUTER: DurableObjectNamespace<RouterCoordinator>;
  ROUTER_CONFIG: KVNamespace;
  CLIENT_KEYS_JSON: string;
  ROUTES_JSON?: string;
  GPT_ROUTES_JSON?: string;
  LINGSUAN_TOTAL_CONCURRENCY?: string;
  WAW_TOTAL_CONCURRENCY?: string;
  TOPAPI_TOTAL_CONCURRENCY?: string;
  PROVIDER_CONCURRENCY_JSON?: string;
  ROUTER_CONFIG_JSON?: string;
  [name: string]: unknown;
}

const json = (value: unknown, status = 200): Response => Response.json(value, { status });
const routeKey = (id: string): string => `health:${id}`;
const reservationKey = (id: string): string => `reservation:${id}`;

function routes(env: Env): RouteConfig[] {
  const parse = (name: string, value: unknown): unknown[] => {
    if (Array.isArray(value)) return value;
    const parsed: unknown = JSON.parse(String(value || "[]"));
    if (!Array.isArray(parsed)) throw new Error(`${name} 必须是数组`);
    return parsed;
  };
  let unifiedRoutes: unknown[] = [];
  if (env.ROUTER_CONFIG_JSON) {
    const unified = JSON.parse(env.ROUTER_CONFIG_JSON) as unknown;
    if (!unified || typeof unified !== "object" || Array.isArray(unified)) throw new Error("ROUTER_CONFIG_JSON 必须是对象");
    unifiedRoutes = parse("ROUTER_CONFIG_JSON.routes", (unified as Record<string, unknown>).routes);
  }
  const routeBundles = Object.entries(env)
    .filter(([name, value]) => name.startsWith("ROUTE_BUNDLE_") && name.endsWith("_JSON") && typeof value === "string")
    .sort(([left], [right]) => left.localeCompare(right))
    .flatMap(([name, value]) => parse(name, value));
  const parsed = unifiedRoutes.length
    ? unifiedRoutes
    : [
        ...parse("ROUTES_JSON", env.ROUTES_JSON),
        ...parse("GPT_ROUTES_JSON", env.GPT_ROUTES_JSON),
        ...routeBundles,
      ];
  const configured = parsed.filter((item): item is RouteConfig => {
    if (!item || typeof item !== "object") return false;
    const candidate = item as Record<string, unknown>;
    return typeof candidate.id === "string" &&
      typeof candidate.provider === "string" && typeof candidate.model === "string" &&
      typeof candidate.base_url === "string" && typeof candidate.api_key_secret === "string";
  });
  const ids = new Set<string>();
  return configured.filter((route) => {
    if (ids.has(route.id)) throw new Error(`路线 ID 重复：${route.id}`);
    ids.add(route.id);
    return true;
  });
}

function routeProtocol(route: RouteConfig): "chat_completions" | "responses" {
  return route.api_protocol === "responses" ? "responses" : "chat_completions";
}

function routeImageAdapter(route: RouteConfig): ImageAdapter {
  return route.image_adapter || "images";
}

async function sha256(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((part) => part.toString(16).padStart(2, "0")).join("");
}

function equalHash(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return difference === 0;
}

async function authenticate(request: Request, env: Env): Promise<string> {
  const supplied = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "") || "";
  if (!supplied) return "";
  const configured: unknown = JSON.parse(String(env.CLIENT_KEYS_JSON || "{}"));
  if (!configured || typeof configured !== "object" || Array.isArray(configured)) return "";
  const suppliedHash = await sha256(supplied);
  for (const [key, userId] of Object.entries(configured as Record<string, unknown>)) {
    if (equalHash(suppliedHash, await sha256(key))) return String(userId || "");
  }
  return "";
}

function switchable(status: number, body: unknown): boolean {
  if ([413, 422].includes(status)) return false;
  if (status === 400) {
    const detail = JSON.stringify(body || {}).toLowerCase();
    return [
      "upstream_error", "upstream request failed", "model_not_found", "model not found",
      "unsupported model", "model is not supported", "overloaded", "unavailable",
    ].some((marker) => detail.includes(marker));
  }
  if ([401, 403, 408, 409, 425, 429].includes(status) || status >= 500) return true;
  if (status >= 300) return true;
  const payload = body as { choices?: Array<{ message?: { content?: unknown; tool_calls?: unknown[] } }>; output?: unknown[] } | null;
  if (!payload) return true;
  const validChoice = Array.isArray(payload.choices) && payload.choices.some((choice) => {
    const content = choice?.message?.content;
    return (typeof content === "string" && content.trim().length > 0) ||
      (Array.isArray(content) && content.length > 0) ||
      (Array.isArray(choice?.message?.tool_calls) && choice.message.tool_calls.length > 0);
  });
  return !validChoice && !(Array.isArray(payload.output) && payload.output.length > 0);
}

function dataUriFromText(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value || "");
  return text.match(/data:image\/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+/)?.[0] || "";
}

function normalizedImage(body: Record<string, unknown> | null): { data: Array<Record<string, unknown>> } | null {
  if (!body) return null;
  const data = Array.isArray(body.data) ? body.data : [];
  const direct = data.find((item) => item && typeof item === "object" && (
    typeof (item as Record<string, unknown>).b64_json === "string" || typeof (item as Record<string, unknown>).url === "string"
  ));
  if (direct) return { data: [direct as Record<string, unknown>] };

  const output = body.output && typeof body.output === "object" ? body.output as Record<string, unknown> : {};
  const choices = Array.isArray(output.choices) ? output.choices : [];
  for (const choice of choices) {
    const message = choice && typeof choice === "object" ? (choice as Record<string, unknown>).message : null;
    const content = message && typeof message === "object" ? (message as Record<string, unknown>).content : null;
    const parts = Array.isArray(content) ? content : [];
    for (const part of parts) {
      if (!part || typeof part !== "object") continue;
      const record = part as Record<string, unknown>;
      const url = typeof record.image === "string"
        ? record.image
        : typeof record.image_url === "string"
          ? record.image_url
          : typeof record.url === "string"
            ? record.url
            : "";
      if (url.startsWith("data:") && url.includes(",")) return { data: [{ b64_json: url.split(",", 2)[1].replace(/\s+/g, "") }] };
      if (url) return { data: [{ url }] };
    }
  }

  const embedded = dataUriFromText(body);
  if (!embedded) return null;
  const comma = embedded.indexOf(",");
  return comma >= 0 ? { data: [{ b64_json: embedded.slice(comma + 1).replace(/\s+/g, "") }] } : null;
}

function dashscopeEndpoint(baseUrl: string): string {
  const base = baseUrl.replace(/\/$/, "");
  const marker = "/compatible-mode/v1";
  if (base.includes(marker)) return `${base.split(marker, 1)[0]}/api/v1/services/aigc/multimodal-generation/generation`;
  if (base.endsWith("/api/v1")) return `${base}/services/aigc/multimodal-generation/generation`;
  return `${base}/api/v1/services/aigc/multimodal-generation/generation`;
}

function imageRequest(route: RouteConfig, payload: Record<string, unknown>): { url: string; body: Record<string, unknown> } {
  const base = route.base_url.replace(/\/$/, "");
  const prompt = String(payload.prompt || "");
  const size = String(payload.size || route.image_size || "1024x1024");
  const adapter = routeImageAdapter(route);
  if (adapter === "dashscope") {
    return {
      url: dashscopeEndpoint(base),
      body: {
        model: route.model,
        input: { messages: [{ role: "user", content: [{ text: prompt }] }] },
        parameters: { size: route.image_size || size, n: 1, watermark: false },
      },
    };
  }
  if (adapter === "embedded_chat") {
    return { url: `${base}/chat/completions`, body: { model: route.model, messages: [{ role: "user", content: prompt }], max_tokens: 1024 } };
  }
  if (adapter === "embedded_responses") {
    return { url: `${base}/responses`, body: { model: route.model, input: prompt, max_output_tokens: 1024 } };
  }
  const body: Record<string, unknown> = { ...payload, model: route.model, prompt, size };
  if (adapter === "ark_seedream") {
    body.size = route.image_size || "2048x2048";
    delete body.n;
    delete body.response_format;
    body.output_format = "png";
    body.watermark = false;
    body.sequential_image_generation = "disabled";
  }
  return { url: `${base}/images/generations`, body };
}

export class RouterCoordinator extends DurableObject<Env> {
  private configuredRoutes: RouteConfig[];

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.configuredRoutes = routes(env);
  }

  private async refreshConfig(): Promise<void> {
    const unified = await this.env.ROUTER_CONFIG.get("routes");
    if (unified) this.configuredRoutes = routes({ ...this.env, ROUTER_CONFIG_JSON: unified });
  }

  private async reservations(): Promise<Reservation[]> {
    const stored = await this.ctx.storage.list<Reservation>({ prefix: "reservation:" });
    const now = Date.now();
    const active: Reservation[] = [];
    for (const [key, value] of stored) {
      if (value.expires_at <= now || value.status === "finished") {
        await this.ctx.storage.delete(key);
      } else {
        active.push(value);
      }
    }
    return active;
  }

  private async ranked(family: string, capability: Capability, excluded = new Set<string>()): Promise<RouteConfig[]> {
    const now = Date.now();
    const candidates = this.configuredRoutes.filter((route) => (
      route.enabled && route.family === family && route.capabilities.includes(capability) && !excluded.has(route.id)
    ));
    const scored = await Promise.all(candidates.map(async (route) => ({
      route,
      health: await this.ctx.storage.get<Health>(routeKey(route.id)) || {
        samples: 0, successes: 0, latency_total_ms: 0, consecutive_failures: 0, cooldown_until: 0,
      },
    })));
    return scored
      .filter(({ health }) => health.cooldown_until <= now)
      .sort((left, right) => {
        if (left.route.priority !== right.route.priority) return left.route.priority - right.route.priority;
        const leftExploring = left.health.samples < 5;
        const rightExploring = right.health.samples < 5;
        if (leftExploring !== rightExploring) return leftExploring ? -1 : 1;
        if (leftExploring && left.health.samples !== right.health.samples) return left.health.samples - right.health.samples;
        const leftSuccess = left.health.samples ? left.health.successes / left.health.samples : 0;
        const rightSuccess = right.health.samples ? right.health.successes / right.health.samples : 0;
        if (leftSuccess !== rightSuccess) return rightSuccess - leftSuccess;
        const leftLatency = left.health.samples ? left.health.latency_total_ms / left.health.samples : Number.MAX_SAFE_INTEGER;
        const rightLatency = right.health.samples ? right.health.latency_total_ms / right.health.samples : Number.MAX_SAFE_INTEGER;
        return leftLatency - rightLatency || left.route.id.localeCompare(right.route.id);
      })
      .map(({ route }) => route);
  }

  private providerPool(route: RouteConfig): string {
    return route.provider_pool || route.provider;
  }

  private providerLimit(route: RouteConfig): number {
    const pool = this.providerPool(route);
    let configuredByPool: Record<string, unknown> = {};
    const configuredJson = this.env.PROVIDER_CONCURRENCY_JSON || (() => {
      if (!this.env.ROUTER_CONFIG_JSON) return "";
      try {
        const unified = JSON.parse(this.env.ROUTER_CONFIG_JSON) as Record<string, unknown>;
        return typeof unified.provider_concurrency === "object" && unified.provider_concurrency !== null
          ? JSON.stringify(unified.provider_concurrency)
          : "";
      } catch {
        return "";
      }
    })();
    if (configuredJson) {
      try {
        const parsed = JSON.parse(configuredJson);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) configuredByPool = parsed as Record<string, unknown>;
      } catch {
        configuredByPool = {};
      }
    }
    const genericValue = configuredByPool[pool];
    const legacyValue = pool === "lingsuan"
      ? this.env.LINGSUAN_TOTAL_CONCURRENCY
      : pool === "wawapi"
        ? this.env.WAW_TOTAL_CONCURRENCY
        : pool === "topapi"
          ? this.env.TOPAPI_TOTAL_CONCURRENCY
          : undefined;
    const environmentLimit = Number(genericValue ?? legacyValue ?? 0);
    if (Number.isInteger(environmentLimit) && environmentLimit > 0) return environmentLimit;
    const configured = this.configuredRoutes
      .filter((item) => item.enabled && this.providerPool(item) === pool)
      .map((item) => Number(item.provider_concurrency || 0))
      .filter((value) => Number.isInteger(value) && value > 0);
    return configured.length ? Math.min(...configured) : 0;
  }

  private modelLimit(route: RouteConfig): number {
    const configured = Number(route.model_concurrency || 0);
    return Number.isInteger(configured) && configured > 0 ? configured : this.providerLimit(route);
  }

  private capacityAvailable(route: RouteConfig, active: Reservation[]): boolean {
    const providerLimit = this.providerLimit(route);
    const modelLimit = this.modelLimit(route);
    if (providerLimit <= 0 || modelLimit <= 0) return false;
    const providerActive = active.filter((item) => {
      const held = this.configuredRoutes.find((candidate) => candidate.id === item.route_id);
      return held && this.providerPool(held) === this.providerPool(route) && ["ready", "executing"].includes(item.status);
    }).length;
    const modelActive = active.filter((item) => item.route_id === route.id && ["ready", "executing"].includes(item.status)).length;
    return providerActive < providerLimit &&
      modelActive < modelLimit;
  }

  private async admit(reservation: Reservation): Promise<Reservation> {
    if (reservation.status !== "waiting") return reservation;
    const all = await this.reservations();
    const older = all.some((item) => item.status === "waiting" && item.created_at < reservation.created_at);
    if (older) return reservation;
    const ranked = await this.ranked(reservation.family, reservation.capability);
    const selected = ranked.find((route) => this.capacityAvailable(route, all));
    if (!selected) return reservation;
    const ready: Reservation = {
      ...reservation,
      status: "ready",
      route_id: selected.id,
      lease: crypto.randomUUID(),
      expires_at: Date.now() + 30_000,
    };
    await this.ctx.storage.put(reservationKey(ready.id), ready);
    return ready;
  }

  private publicReservation(reservation: Reservation): Record<string, unknown> {
    const route = this.configuredRoutes.find((item) => item.id === reservation.route_id);
    return {
      reservation_id: reservation.id,
      status: reservation.status,
      poll_after_ms: 500,
      lease: reservation.lease || "",
      provider: route?.provider || "",
      model: route?.model || "",
      api_protocol: route ? routeProtocol(route) : "",
      image_adapter: route ? routeImageAdapter(route) : "",
      image_size: route?.image_size || "",
      thinking_minimum: route?.thinking_minimum || "",
    };
  }

  private async record(route: RouteConfig, success: boolean, elapsedMs: number): Promise<void> {
    const prior = await this.ctx.storage.get<Health>(routeKey(route.id)) || {
      samples: 0, successes: 0, latency_total_ms: 0, consecutive_failures: 0, cooldown_until: 0,
    };
    const failures = success ? 0 : prior.consecutive_failures + 1;
    await this.ctx.storage.put(routeKey(route.id), {
      samples: prior.samples + 1,
      successes: prior.successes + Number(success),
      latency_total_ms: prior.latency_total_ms + elapsedMs,
      consecutive_failures: failures,
      cooldown_until: success ? 0 : Date.now() + Math.min(600_000, 30_000 * (2 ** Math.min(4, failures - 1))),
    } satisfies Health);
  }

  private async execute(request: Request, userId: string, requestedProtocol: "chat_completions" | "responses"): Promise<Response> {
    const lease = request.headers.get("x-smart-router-lease") || "";
    const requestId = request.headers.get("x-smart-router-request-id") || "";
    const reservation = await this.ctx.storage.get<Reservation>(reservationKey(requestId));
    if (!reservation || reservation.user_id !== userId || reservation.lease !== lease || reservation.expires_at <= Date.now()) {
      return json({ error: "智能路由预约已失效，请重新发起请求。" }, 409);
    }
    const payload = await request.json<Record<string, unknown>>();
    reservation.status = "executing";
    reservation.expires_at = Date.now() + 15 * 60_000;
    await this.ctx.storage.put(reservationKey(reservation.id), reservation);
    const attempted = new Set<string>();
    const attempts: Record<string, unknown>[] = [];
    let selectedId = reservation.route_id || "";
    const capacityWaitStarted = Date.now();
    let capacityTimedOut = false;
    try {
      while (true) {
        const ordered = (await this.ranked(reservation.family, reservation.capability, attempted))
          .filter((route) => routeProtocol(route) === requestedProtocol);
        const active = (await this.reservations()).filter((item) => item.id !== reservation.id);
        const selected = this.configuredRoutes.find((route) => (
          route.id === selectedId && !attempted.has(route.id) && routeProtocol(route) === requestedProtocol
        )) || ordered.find((route) => this.capacityAvailable(route, active));
        if (!selected && ordered.length && Date.now() - capacityWaitStarted < 60_000) {
          await new Promise((resolve) => setTimeout(resolve, 250));
          continue;
        }
        if (!selected) {
          capacityTimedOut = ordered.length > 0;
          break;
        }
        attempted.add(selected.id);
        reservation.route_id = selected.id;
        await this.ctx.storage.put(reservationKey(reservation.id), reservation);
        const secret = String(this.env[selected.api_key_secret] || "");
        if (!secret) {
          attempts.push({ provider: selected.provider, model: selected.model, error: "上游 Key 未配置" });
          await this.record(selected, false, 0);
          selectedId = "";
          continue;
        }
        const started = Date.now();
        let response: Response;
        let body: Record<string, unknown> | null = null;
        try {
          const upstreamHeaders: Record<string, string> = { "Content-Type": "application/json" };
          upstreamHeaders[selected.auth_header || "Authorization"] = `${selected.auth_scheme ?? "Bearer "}${secret}`;
          const upstreamPath = routeProtocol(selected) === "responses" ? "responses" : "chat/completions";
          response = await fetch(`${selected.base_url.replace(/\/$/, "")}/${upstreamPath}`, {
            method: "POST",
            headers: upstreamHeaders,
            body: JSON.stringify({ ...payload, model: selected.model }),
            signal: AbortSignal.timeout(10 * 60_000),
          });
          body = await response.json<Record<string, unknown>>().catch(() => null);
        } catch (error) {
          response = new Response(null, { status: 599 });
          body = { error: error instanceof Error ? error.message : "上游网络错误" };
        }
        const elapsedMs = Date.now() - started;
        const okay = response.ok && !switchable(response.status, body);
        await this.record(selected, okay, elapsedMs);
        if (okay && body) {
          const usage = body.usage && typeof body.usage === "object" ? body.usage as Record<string, unknown> : {};
          await this.ctx.storage.put(`usage:${userId}:${Date.now()}:${crypto.randomUUID()}`, {
            user_id: userId, provider: selected.provider, model: selected.model,
            task_id: reservation.task_id || "", stage: reservation.stage || "", active_item: reservation.active_item || "",
            prompt_tokens: usage.prompt_tokens ?? usage.input_tokens ?? null,
            completion_tokens: usage.completion_tokens ?? usage.output_tokens ?? null,
            created_at: Date.now(),
          });
          return json({ ...body, model: selected.model, _smart_route: {
            provider: selected.provider, model: selected.model, route_id: selected.id,
            attempts: [...attempts, { provider: selected.provider, model: selected.model, status: "succeeded", elapsed_ms: elapsedMs }],
          }});
        }
        const errorText = String((body?.error as { message?: unknown } | undefined)?.message || body?.error || `HTTP ${response.status}`).slice(0, 240);
        attempts.push({ provider: selected.provider, model: selected.model, status: response.status, error: errorText, elapsed_ms: elapsedMs });
        if (!switchable(response.status, body)) return json({ ...(body || {}), _smart_route: { attempts } }, response.status);
        selectedId = "";
      }
      const detail = attempts.map((item) => `${item.provider}/${item.model}：${item.error || item.status}`).join("；");
      const headline = capacityTimedOut
        ? "剩余候选模型等待并发超过 60 秒，当前阶段已停止；任务本身没有异常。"
        : "所有可用模型均调用失败。";
      return json({
        error: `${headline}${detail ? ` 已尝试：${detail}` : ""}`,
        detail,
        _smart_route: { attempts },
      }, 503);
    } finally {
      reservation.status = "finished";
      reservation.expires_at = Date.now() + 60_000;
      await this.ctx.storage.put(reservationKey(reservation.id), reservation);
    }
  }

  private async executeImage(request: Request, userId: string, capability: "image_generation" | "image_edit"): Promise<Response> {
    const lease = request.headers.get("x-smart-router-lease") || "";
    const requestId = request.headers.get("x-smart-router-request-id") || "";
    const reservation = await this.ctx.storage.get<Reservation>(reservationKey(requestId));
    if (!reservation || reservation.user_id !== userId || reservation.lease !== lease || reservation.expires_at <= Date.now()) {
      return json({ error: "智能路由预约已失效，请重新发起请求。" }, 409);
    }
    if (reservation.capability !== capability) return json({ error: "智能路由预约能力与请求不一致。" }, 409);

    const generationPayload = capability === "image_generation"
      ? await request.json<Record<string, unknown>>()
      : null;
    const editPayload = capability === "image_edit" ? await request.formData() : null;
    reservation.status = "executing";
    reservation.expires_at = Date.now() + 15 * 60_000;
    await this.ctx.storage.put(reservationKey(reservation.id), reservation);
    const attempted = new Set<string>();
    const attempts: Record<string, unknown>[] = [];
    let selectedId = reservation.route_id || "";
    const capacityWaitStarted = Date.now();
    let capacityTimedOut = false;
    try {
      while (true) {
        const ordered = await this.ranked(reservation.family, capability, attempted);
        const active = (await this.reservations()).filter((item) => item.id !== reservation.id);
        const selected = this.configuredRoutes.find((route) => (
          route.id === selectedId && !attempted.has(route.id) && route.capabilities.includes(capability)
        )) || ordered.find((route) => this.capacityAvailable(route, active));
        if (!selected && ordered.length && Date.now() - capacityWaitStarted < 60_000) {
          await new Promise((resolve) => setTimeout(resolve, 250));
          continue;
        }
        if (!selected) {
          capacityTimedOut = ordered.length > 0;
          break;
        }
        attempted.add(selected.id);
        reservation.route_id = selected.id;
        await this.ctx.storage.put(reservationKey(reservation.id), reservation);
        const secret = String(this.env[selected.api_key_secret] || "");
        if (!secret) {
          attempts.push({ provider: selected.provider, model: selected.model, error: "上游 Key 未配置" });
          await this.record(selected, false, 0);
          selectedId = "";
          continue;
        }

        const started = Date.now();
        let response: Response;
        let body: Record<string, unknown> | null = null;
        try {
          const upstreamHeaders: Record<string, string> = {};
          upstreamHeaders[selected.auth_header || "Authorization"] = `${selected.auth_scheme ?? "Bearer "}${secret}`;
          if (capability === "image_generation") {
            const prepared = imageRequest(selected, generationPayload || {});
            upstreamHeaders["Content-Type"] = "application/json";
            response = await fetch(prepared.url, {
              method: "POST", headers: upstreamHeaders, body: JSON.stringify(prepared.body),
              signal: AbortSignal.timeout(10 * 60_000),
            });
          } else {
            const form = new FormData();
            for (const [name, value] of editPayload?.entries() || []) {
              if (name === "model") continue;
              form.append(name, value);
            }
            form.set("model", selected.model);
            response = await fetch(`${selected.base_url.replace(/\/$/, "")}/images/edits`, {
              method: "POST", headers: upstreamHeaders, body: form,
              signal: AbortSignal.timeout(10 * 60_000),
            });
          }
          body = await response.json<Record<string, unknown>>().catch(() => null);
        } catch (error) {
          response = new Response(null, { status: 599 });
          body = { error: error instanceof Error ? error.message : "上游网络错误" };
        }
        const elapsedMs = Date.now() - started;
        const normalized = response.ok ? normalizedImage(body) : null;
        const okay = response.ok && normalized !== null;
        await this.record(selected, okay, elapsedMs);
        if (okay && normalized) {
          await this.ctx.storage.put(`usage:${userId}:${Date.now()}:${crypto.randomUUID()}`, {
            user_id: userId, provider: selected.provider, model: selected.model,
            task_id: reservation.task_id || "", stage: reservation.stage || "", active_item: reservation.active_item || "",
            image_count: 1, operation: capability, created_at: Date.now(),
          });
          return json({ ...normalized, model: selected.model, _smart_route: {
            provider: selected.provider, model: selected.model, route_id: selected.id,
            image_adapter: routeImageAdapter(selected),
            attempts: [...attempts, { provider: selected.provider, model: selected.model, status: "succeeded", elapsed_ms: elapsedMs }],
          }});
        }
        const errorText = String(
          (body?.error as { message?: unknown } | undefined)?.message || body?.error ||
          (response.ok ? "响应中没有有效图片" : `HTTP ${response.status}`)
        ).slice(0, 240);
        attempts.push({ provider: selected.provider, model: selected.model, status: response.status, error: errorText, elapsed_ms: elapsedMs });
        if (!switchable(response.status, body) && !response.ok) {
          return json({ ...(body || {}), _smart_route: { attempts } }, response.status);
        }
        selectedId = "";
      }
      const detail = attempts.map((item) => `${item.provider}/${item.model}：${item.error || item.status}`).join("；");
      const headline = capacityTimedOut
        ? "剩余候选模型等待并发超过 60 秒，当前阶段已停止；任务本身没有异常。"
        : `所有可用模型均${capability === "image_edit" ? "无法完成图片编辑" : "未能生成图片"}。`;
      return json({ error: `${headline}${detail ? ` 已尝试：${detail}` : ""}`, detail, _smart_route: { attempts } }, 503);
    } finally {
      reservation.status = "finished";
      reservation.expires_at = Date.now() + 60_000;
      await this.ctx.storage.put(reservationKey(reservation.id), reservation);
    }
  }

  private async handleFetch(request: Request): Promise<Response> {
    await this.refreshConfig();
    const url = new URL(request.url);
    const userId = request.headers.get("x-router-user") || "";
    if (!userId) return json({ error: "未授权" }, 401);
    if (request.method === "POST" && url.pathname === "/v1/reservations") {
      const input = await request.json<Record<string, unknown>>();
      const id = String(input.request_id || crypto.randomUUID());
      const existing = await this.ctx.storage.get<Reservation>(reservationKey(id));
      if (existing) return json(this.publicReservation(await this.admit(existing)));
      const reservation: Reservation = {
        id, user_id: userId, family: String(input.family || "gemini"),
        capability: ["vision", "image_generation", "image_edit"].includes(String(input.capability || ""))
          ? String(input.capability) as Capability
          : "text",
        status: "waiting", created_at: Date.now(), expires_at: Date.now() + 60_000,
        task_id: String(input.task_id || ""), stage: String(input.stage || ""), active_item: String(input.active_item || ""),
      };
      await this.ctx.storage.put(reservationKey(id), reservation);
      return json(this.publicReservation(await this.admit(reservation)));
    }
    if (request.method === "GET" && url.pathname.startsWith("/v1/reservations/")) {
      const id = decodeURIComponent(url.pathname.slice("/v1/reservations/".length));
      const reservation = await this.ctx.storage.get<Reservation>(reservationKey(id));
      if (!reservation || reservation.user_id !== userId) return json({ error: "预约不存在或已过期" }, 404);
      return json(this.publicReservation(await this.admit(reservation)));
    }
    if (request.method === "POST" && url.pathname === "/v1/chat/completions") return this.execute(request, userId, "chat_completions");
    if (request.method === "POST" && url.pathname === "/v1/responses") return this.execute(request, userId, "responses");
    if (request.method === "POST" && url.pathname === "/v1/images/generations") return this.executeImage(request, userId, "image_generation");
    if (request.method === "POST" && url.pathname === "/v1/images/edits") return this.executeImage(request, userId, "image_edit");
    if (request.method === "GET" && url.pathname === "/v1/usage") {
      const stored = await this.ctx.storage.list<Record<string, unknown>>({ prefix: `usage:${userId}:` });
      const byRoute: Record<string, { calls: number; prompt_tokens: number; completion_tokens: number }> = {};
      for (const value of stored.values()) {
        const key = `${String(value.provider || "")}/${String(value.model || "")}`;
        const row = byRoute[key] ||= { calls: 0, prompt_tokens: 0, completion_tokens: 0 };
        row.calls += 1;
        row.prompt_tokens += Number(value.prompt_tokens || 0);
        row.completion_tokens += Number(value.completion_tokens || 0);
      }
      return json({ user_id: userId, total_calls: stored.size, by_route: byRoute });
    }
    if (request.method === "GET" && url.pathname === "/v1/router-status") {
      const family = url.searchParams.get("family") || "gemini";
      const active = await this.reservations();
      const rows = await Promise.all(this.configuredRoutes.filter((route) => route.family === family).map(async (route) => {
        const health = await this.ctx.storage.get<Health>(routeKey(route.id)) || { samples: 0, successes: 0, latency_total_ms: 0, consecutive_failures: 0, cooldown_until: 0 };
        return {
          id: route.id, provider: route.provider, model: route.model, priority: route.priority,
          enabled: route.enabled, capabilities: route.capabilities, api_protocol: routeProtocol(route),
          samples: health.samples,
          success_rate: health.samples ? Math.round(health.successes * 10000 / health.samples) / 100 : null,
          average_latency_ms: health.samples ? Math.round(health.latency_total_ms / health.samples) : null,
          cooldown_until: health.cooldown_until || null,
          provider_active: active.filter((item) => {
            const held = this.configuredRoutes.find((candidate) => candidate.id === item.route_id);
            return held && this.providerPool(held) === this.providerPool(route) && ["ready", "executing"].includes(item.status);
          }).length,
          provider_limit: this.providerLimit(route),
          model_active: active.filter((item) => item.route_id === route.id && ["ready", "executing"].includes(item.status)).length,
          model_limit: this.modelLimit(route),
        };
      }));
      return json({ family, routes: rows });
    }
    return json({ error: "未找到接口" }, 404);
  }

  async fetch(request: Request): Promise<Response> {
    try {
      return await this.handleFetch(request);
    } catch (error) {
      const detail = error instanceof Error
        ? `${error.name}: ${error.message}${error.stack ? `\n${error.stack}` : ""}`
        : String(error);
      // Keep unexpected Worker exceptions observable to the caller instead of
      // allowing Cloudflare to replace them with an opaque 1101 response.
      const safeDetail = detail.slice(0, 4000);
      return json({ error: `智能路由 Worker 内部异常：${safeDetail}`, detail: safeDetail }, 500);
    }
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      const userId = await authenticate(request, env);
      if (!userId) return json({ error: "用户访问 Key 无效" }, 401);
      const stub = env.ROUTER.getByName("shared-upstream-credential-pool");
      const headers = new Headers(request.headers);
      headers.set("x-router-user", userId);
      return await stub.fetch(new Request(request, { headers }));
    } catch (error) {
      const detail = error instanceof Error
        ? `${error.name}: ${error.message}${error.stack ? `\n${error.stack}` : ""}`
        : String(error);
      const safeDetail = detail.slice(0, 4000);
      return json({ error: `智能路由入口内部异常：${safeDetail}`, detail: safeDetail }, 500);
    }
  },
} satisfies ExportedHandler<Env>;
