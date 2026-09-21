// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { useCallback, useEffect, useRef, useState } from "react";
import { api, mutation } from "../api";
import { wire, type Project } from "./types";

export function useDocument(initial: Project) {
	const [project, setProject] = useState(initial);
	const current = useRef(initial);
	const generation = useRef(0),
		saved = useRef(0);
	const inFlight = useRef<Promise<Project> | null>(null);
	const errorStatus = useRef<number | undefined>(undefined);
	const mounted = useRef(true);
	const [saving, setSaving] = useState(false),
		[error, setError] = useState("");
	const [dirty, setDirty] = useState(false);
	const undoStack = useRef<Project[]>([]),
		redoStack = useRef<Project[]>([]);
	const key = `onecat:canvas-draft:${initial.id}`;
	const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const persistDraft = useCallback(() => {
		if (draftTimer.current) clearTimeout(draftTimer.current);
		draftTimer.current = null;
		try {
			if (saved.current !== generation.current)
				localStorage.setItem(key, JSON.stringify(wire(current.current)));
		} catch {
			/* Server persistence works when browser storage is full. */
		}
	}, [key]);
	const publish = useCallback((next: Project) => {
		current.current = next;
		if (mounted.current) {
			setProject(next);
			setDirty(generation.current !== saved.current);
		}
	}, []);
	const edit = useCallback(
		(fn: (value: Project) => Project, history = true) => {
			const before = current.current,
				next = fn(before);
			if (next === before) return;
			// Correcting a field should resume autosave. A conflicting tab still
			// requires an explicit reload/copy, so its revision is never overwritten.
			if (errorStatus.current !== 409) setError("");
			if (history) {
				undoStack.current = [...undoStack.current.slice(-39), before];
				redoStack.current = [];
			}
			generation.current++;
			publish(next);
			// A recoverable local draft complements server persistence during disconnects.
			if (draftTimer.current) clearTimeout(draftTimer.current);
			draftTimer.current = setTimeout(persistDraft, 200);
		},
		[persistDraft, publish],
	);
	const save = useCallback((): Promise<Project> => {
		if (inFlight.current) return inFlight.current;
		if (saved.current === generation.current)
			return Promise.resolve(current.current);
		if (mounted.current) {
			setSaving(true);
			setError("");
		}
		const flush = async () => {
			// All callers await the same drain. A generation request can proceed
			// only after every edit made during the pending write has been saved.
			while (saved.current !== generation.current) {
				const version = generation.current,
					snapshot = current.current;
				let result: Project;
				try {
					result = await mutation<Project>(
						`/api/creative/projects/${snapshot.id}`,
						wire(snapshot),
						"PUT",
					);
				} catch (cause) {
					const status = (cause as { status?: number }).status;
					if (
						(status === 400 || status === 422) &&
						version !== generation.current
					)
						continue;
					errorStatus.current = status;
					if (mounted.current) setError((cause as Error).message);
					throw cause;
				}
				saved.current = version;
				errorStatus.current = undefined;
				publish({
					...current.current,
					revision: result.revision,
					updated_at: result.updated_at,
				});
				try {
					if (saved.current === generation.current)
						localStorage.removeItem(key);
					else localStorage.setItem(key, JSON.stringify(wire(current.current)));
				} catch {
					/* Optional browser recovery copy. */
				}
			}
			return current.current;
		};
		inFlight.current = flush().finally(() => {
			inFlight.current = null;
			if (mounted.current) setSaving(false);
		});
		return inFlight.current;
	}, [key, publish]);

	useEffect(() => {
		mounted.current = true;
		return () => {
			persistDraft();
			mounted.current = false;
			void save().catch(() => {});
		};
	}, [save, persistDraft]);
	useEffect(() => {
		if (!dirty || error) return;
		const timer = setTimeout(() => void save().catch(() => {}), 600);
		return () => clearTimeout(timer);
	}, [project, dirty, error, save]);
	useEffect(() => {
		const unload = (event: BeforeUnloadEvent) => {
			persistDraft();
			if (saved.current !== generation.current) {
				event.preventDefault();
				event.returnValue = "";
			}
		};
		const online = () => {
			void save().catch(() => {});
		};
		window.addEventListener("beforeunload", unload);
		window.addEventListener("online", online);
		return () => {
			window.removeEventListener("beforeunload", unload);
			window.removeEventListener("online", online);
		};
	}, [save]);
	const undo = () => {
		const next = undoStack.current.pop();
		if (!next) return;
		redoStack.current.push(current.current);
		edit(
			(p) => ({
				...next,
				revision: p.revision,
				placed_runs: [...new Set([...p.placed_runs, ...next.placed_runs])],
			}),
			false,
		);
	};
	const redo = () => {
		const next = redoStack.current.pop();
		if (!next) return;
		undoStack.current.push(current.current);
		edit(
			(p) => ({
				...next,
				revision: p.revision,
				placed_runs: [...new Set([...p.placed_runs, ...next.placed_runs])],
			}),
			false,
		);
	};
	const reload = async () => {
		const remote = await api<Project>(`/api/creative/projects/${initial.id}`);
		saved.current = ++generation.current;
		setError("");
		publish(remote);
	};
	return {
		project,
		edit,
		save,
		saving,
		dirty,
		error,
		undo,
		redo,
		canUndo: undoStack.current.length > 0,
		canRedo: redoStack.current.length > 0,
		reload,
	};
}
