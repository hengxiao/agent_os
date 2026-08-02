/* REST 封装(docs/WEB-UI.md §6.1):同源相对路径,getJson / postJson + 统一错误处理。 */

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status; // HTTP 状态码;0 = 网络层错误(服务不可达)
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { Accept: "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch (e) {
    throw new ApiError(`网络错误:${e.message}`, 0);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* 非 JSON 响应(如代理错误页):data 置空,按状态码报错 */
  }
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`;
    throw new ApiError(detail, res.status);
  }
  return data;
}

export const getJson = (path) => request(path);

export const postJson = (path, body) =>
  request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const putJson = (path, body) =>
  request(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const deleteJson = (path) => request(path, { method: "DELETE" });
