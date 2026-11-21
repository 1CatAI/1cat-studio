// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import { Component, createRef, type ReactNode, type RefObject } from "react";

export const previewSlide = { duration: 300, easing: "cubic-bezier(.35,0,.22,1)" };

type Props = { open: boolean; enabled: boolean; following: RefObject<boolean>; children: ReactNode };
type Geometry = { left: number; bottom: number; form: DOMRect };

export class ChatPreviewSplit extends Component<Props, {}, Geometry | null> {
  private root = createRef<HTMLDivElement>();
  private flights: Animation[] = [];

  private geometry(): Geometry | null {
    const root = this.root.current;
    const list = root?.querySelector<HTMLElement>(".oc-message-list");
    const viewport = root?.querySelector<HTMLElement>(".oc-conversation");
    const form = root?.querySelector<HTMLElement>(".oc-chat-composer");
    if (!list || !viewport || !form) return null;
    return {
      left: list.getBoundingClientRect().left + parseFloat(getComputedStyle(list).paddingLeft),
      bottom: viewport.getBoundingClientRect().bottom,
      form: form.getBoundingClientRect(),
    };
  }

  getSnapshotBeforeUpdate(previous: Props) {
    return this.props.enabled && previous.open !== this.props.open ? this.geometry() : null;
  }

  componentDidUpdate(previous: Props, _state: {}, from: Geometry | null) {
    if (previous.open === this.props.open && previous.enabled === this.props.enabled) return;
    this.cancelFlights();
    if (!from) return;
    const root = this.root.current!;
    const viewport = root.querySelector<HTMLElement>(".oc-conversation")!;
    // Pin the output in the new layout before measuring. Projecting the entire
    // scrollable message list would count its rewrapped height as travel.
    if (this.props.following.current) viewport.scrollTop = viewport.scrollHeight;
    const to = this.geometry();
    if (!to) return;
    const shell = root.querySelector<HTMLElement>(".oc-conversation-shell")!;
    const form = root.querySelector<HTMLElement>(".oc-chat-composer")!;
    const dy = this.props.following.current ? from.bottom - to.bottom : 0;
    this.flights.push(shell.animate([
      { translate: `${from.left - to.left}px ${dy}px` }, { translate: "0 0" },
    ], previewSlide));
    const resize = Math.abs(from.form.width - to.form.width) > 0.5;
    this.flights.push(form.animate([
      { translate: `${from.form.left - to.form.left}px ${from.form.top - to.form.top}px`,
        ...(resize && { width: `${from.form.width}px` }) },
      { translate: "0 0", ...(resize && { width: `${to.form.width}px` }) },
    ], previewSlide));
  }

  private cancelFlights() {
    this.flights.forEach(animation => animation.cancel());
    this.flights = [];
  }

  componentWillUnmount() { this.cancelFlights(); }

  render() {
    return <div ref={this.root} className="oc-chat-workspace" data-preview-open={this.props.open}>
      {this.props.children}
    </div>;
  }
}
