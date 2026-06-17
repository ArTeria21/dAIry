export type SessionUser = {
  username: string;
};

export async function getCurrentUser(): Promise<SessionUser | null> {
  const response = await fetch("/api/auth/me", {
    credentials: "include",
  });
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw new Error("Session check failed");
  }
  return response.json() as Promise<SessionUser>;
}

export async function login(
  username: string,
  password: string,
): Promise<SessionUser> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error("LOGIN FAILED");
  }
  return response.json() as Promise<SessionUser>;
}
