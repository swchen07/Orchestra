export class SessionRequestError extends Error {
	constructor(
		message: string,
		readonly status: number
	) {
		super(message);
		this.name = 'SessionRequestError';
	}
}

export const isAuthenticationRejection = (error: unknown): boolean =>
	error instanceof SessionRequestError && [401, 403].includes(error.status);

export async function readSessionResponse(response: Response) {
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		throw new SessionRequestError(
			typeof body?.detail === 'string' ? body.detail : '无法验证登录状态，请重试。',
			response.status
		);
	}
	return response.json();
}
