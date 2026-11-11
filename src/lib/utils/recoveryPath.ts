export function recoveryPath(value: string | null | undefined): string {
	if (
		!value ||
		!value.startsWith('/') ||
		value.startsWith('//') ||
		Array.from(value).some((char) => char === '\\' || char.charCodeAt(0) < 32) ||
		value.length > 8192 ||
		value.split(/[?#]/)[0] === '/error'
	)
		return '/';
	return value;
}
