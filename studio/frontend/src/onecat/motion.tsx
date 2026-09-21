// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import {
	Children,
	cloneElement,
	isValidElement,
	createContext,
	useContext,
	useEffect,
	useId,
	useLayoutEffect,
	useState,
	useSyncExternalStore,
	type ReactNode,
	type ComponentProps,
} from "react";
import { MotionConfig, motion } from "motion/react";

export const MOTION = {
	compact: 0.16,
	state: 0.2,
	panel: 0.22,
	ease: [0.2, 0.7, 0.2, 1] as const,
};
const Context = createContext({
	enabled: true,
	preference: true,
	setPreference: (_value: boolean) => {},
});
export const useInterfaceMotion = () => useContext(Context);
const reducedQuery = "(prefers-reduced-motion: reduce)";
const subscribeReduced = (listener: () => void) => {
	const media = matchMedia(reducedQuery);
	media.addEventListener("change", listener);
	return () => media.removeEventListener("change", listener);
};

export function InterfaceMotion({ children }: { children: ReactNode }) {
	const read = () => {
		try {
			return localStorage.getItem("onecat_ui_motion") !== "off";
		} catch {
			return true;
		}
	};
	const [preference, setValue] = useState(read);
	const reduced = useSyncExternalStore(
		subscribeReduced,
		() => matchMedia(reducedQuery).matches,
	);
	const enabled = preference && !reduced;
	const setPreference = (value: boolean) => {
		try {
			localStorage.setItem("onecat_ui_motion", value ? "on" : "off");
		} catch {
			/* Optional browser preference. */
		}
		setValue(value);
	};
	useEffect(() => {
		const changed = (event: StorageEvent) => {
			if (event.key === "onecat_ui_motion") setValue(read());
		};
		window.addEventListener("storage", changed);
		return () => window.removeEventListener("storage", changed);
	}, []);
	useLayoutEffect(() => {
		document.documentElement.dataset.uiMotion = enabled ? "on" : "off";
	}, [enabled]);
	useEffect(() => {
		if (!enabled) return;
		// Animate only explicit disclosure operations, never append/stream/resize events.
		const running = new Map<
			HTMLDetailsElement,
			{ animation?: Animation; target: boolean; frame?: number }
		>();
		const finish = (details: HTMLDetailsElement, target: boolean) => {
			details.open = target;
			details.style.removeProperty("height");
			details.style.removeProperty("overflow");
			delete details.dataset.expanding;
			running.delete(details);
		};
		const click = (event: MouseEvent) => {
			const target = event.target as Element;
			const summary = target.closest("summary");
			const details = summary?.parentElement;
			if (
				!(details instanceof HTMLDetailsElement) ||
				!details.closest(".oc-workspace, .oc-dialog") ||
				target.closest("a,button,input,select") ||
				event.defaultPrevented
			)
				return;
			event.preventDefault();
			const previous = running.get(details);
			const opening = previous ? !previous.target : !details.open;
			const from = details.getBoundingClientRect().height;
			previous?.animation?.cancel();
			if (previous?.frame) cancelAnimationFrame(previous.frame);
			const state: { animation?: Animation; target: boolean; frame?: number } =
				{ target: opening };
			running.set(details, state);
			details.dataset.expanding = String(opening);
			details.style.height = `${from}px`;
			details.style.overflow = "clip";
			details.open = true;
			// The native toggle also mounts lazy reasoning content through React.
			state.frame = requestAnimationFrame(() => {
				state.frame = requestAnimationFrame(() => {
					if (!details.isConnected) {
						finish(details, opening);
						return;
					}
					details.style.height = "auto";
					if (!opening) details.open = false;
					const to = details.getBoundingClientRect().height;
					details.open = true;
					details.style.height = `${from}px`;
					state.animation = details.animate(
						[{ height: `${from}px` }, { height: `${to}px` }],
						{ duration: 200, easing: "cubic-bezier(.2,.7,.2,1)" },
					);
					state.animation.onfinish = () => finish(details, opening);
				});
			});
		};
		document.addEventListener("click", click);
		return () => {
			document.removeEventListener("click", click);
			running.forEach((state, details) => {
				state.animation?.cancel();
				if (state.frame) cancelAnimationFrame(state.frame);
				finish(details, state.target);
			});
		};
	}, [enabled]);
	return (
		<Context.Provider value={{ enabled, preference, setPreference }}>
			<MotionConfig
				reducedMotion={enabled ? "user" : "always"}
				transition={{ duration: enabled ? MOTION.state : 0, ease: MOTION.ease }}
			>
				{children}
			</MotionConfig>
		</Context.Provider>
	);
}

/** Only a semantic phase change animates; live numeric updates keep their DOM and position. */
export function Phase({
	phase,
	children,
	className = "",
}: { phase: string; children: ReactNode; className?: string }) {
	const { enabled } = useInterfaceMotion();
	return (
		<motion.span
			key={phase}
			className={`oc-phase ${className}`}
			initial={enabled ? { opacity: 0.45 } : false}
			animate={{ opacity: 1 }}
			transition={{ duration: enabled ? MOTION.state : 0 }}
		>
			{children}
		</motion.span>
	);
}

/** A moving selection background; the controls themselves never move or remount. */
export function Segments({
	children,
	className = "",
	...props
}: ComponentProps<"div">) {
	const id = useId();
	return (
		<div {...props} className={`oc-segments oc-motion-segments ${className}`}>
			{Children.map(children, (child) => {
				if (!isValidElement<ComponentProps<"button">>(child)) return child;
				const selected =
					child.props["aria-pressed"] || child.props["aria-checked"] || child.props["aria-selected"];
				return cloneElement(
					child,
					{},
					<>
						{selected && (
							<motion.span
								layoutId={`segment-${id}`}
								className="oc-segment-highlight"
							/>
						)}
						<span className="oc-segment-label">{child.props.children}</span>
					</>,
				);
			})}
		</div>
	);
}
