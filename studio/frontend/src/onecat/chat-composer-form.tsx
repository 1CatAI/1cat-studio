// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Component, createRef, type FormEventHandler, type ReactNode } from "react";

type Props = {
  placement: "welcome" | "first-send" | "conversation";
  threadId?: string;
  motionEnabled: boolean;
  onSubmit: FormEventHandler<HTMLFormElement>;
  children: ReactNode;
};

// Read the live form before React replaces the welcome layout. This also captures
// its painted position when a rejected first send interrupts a move in progress.
export class ChatComposerForm extends Component<Props, {}, DOMRect | null> {
  private form = createRef<HTMLFormElement>();
  private flight?: Animation;

  getSnapshotBeforeUpdate(previous: Props) {
    const { placement, threadId, motionEnabled } = this.props;
    const docking = previous.placement === "welcome" && placement === "first-send";
    const returning = previous.placement === "first-send" && placement === "welcome";
    return motionEnabled && previous.threadId === threadId && (docking || returning)
      ? this.form.current!.getBoundingClientRect()
      : null;
  }

  componentDidUpdate(previous: Props, _state: {}, start: DOMRect | null) {
    const { motionEnabled, placement, threadId } = this.props;
    if (!motionEnabled || (previous.threadId !== threadId && (previous.threadId || placement === "welcome"))) {
      this.flight?.cancel();
    }
    if (!start) return;
    const form = this.form.current!;
    this.flight?.cancel();
    const end = form.getBoundingClientRect();
    // The sent layout is bottom-aligned; the empty layout flows from the top.
    const dy = placement === "first-send" ? start.bottom - end.bottom : start.top - end.top;
    const dx = start.left - end.left;
    const resizing = Math.abs(start.height - end.height) > 0.5;
    this.flight = form.animate([
      { transform: `translate(${dx}px, ${dy}px)`, ...(resizing && { height: `${start.height}px` }) },
      { transform: "none", ...(resizing && { height: `${end.height}px` }) },
    ], {
      duration: placement === "first-send" ? 540 : 440,
      easing: "cubic-bezier(.38,.02,.20,1)",
    });
  }

  componentWillUnmount() {
    this.flight?.cancel();
  }

  render() {
    return <form className="oc-chat-composer" ref={this.form} onSubmit={this.props.onSubmit}>
      {this.props.children}
    </form>;
  }
}
