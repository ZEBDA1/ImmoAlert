import { useEffect, useRef, useState } from "react";
import { Building2 } from "lucide-react";
import { m } from "motion/react";
import { useMotionSettings } from "./motion";

type ImageProps = {
  src: string;
  alt: string;
  loading?: "eager" | "lazy";
  announce?: boolean;
};

/** Each URL owns its loading state, including cached images and late decode events. */
export default function ProgressiveImage(props: ImageProps) {
  return <ImageFrame key={props.src} {...props}/>;
}

function ImageFrame({ src, alt, loading = "lazy", announce = false }: ImageProps) {
  const { reduced } = useMotionSettings();
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const image = useRef<HTMLImageElement>(null);
  const mounted = useRef(true);

  async function reveal(node: HTMLImageElement) {
    // Decode before revealing: a completed request alone can still show a blank frame.
    try { await node.decode(); } catch { /* Some browsers cannot decode an already displayed image. */ }
    if (mounted.current) setStatus(node.naturalWidth > 0 ? "ready" : "error");
  }

  useEffect(() => {
    mounted.current = true;
    if (image.current?.complete) {
      if (image.current.naturalWidth > 0) void reveal(image.current);
      else if (image.current.currentSrc) setStatus("error");
    }
    return () => { mounted.current = false; };
  }, []);

  return <div className={`progressive-image is-${status}`} data-image-state={status} aria-busy={status === "loading"}>
    <span className="photo-skeleton" aria-hidden="true"/>
    {status !== "error" && <m.img ref={image} src={src} alt={alt} loading={loading} decoding="async"
      initial={{ opacity: 0 }} animate={{ opacity: status === "ready" ? 1 : 0 }}
      transition={{ duration: reduced ? 0 : .24, ease: [.22, 1, .36, 1] }}
      onLoad={event => void reveal(event.currentTarget)} onError={() => setStatus("error")}/>}
    {status === "error" && <div className="property-placeholder"><Building2 size={32} strokeWidth={1.3}/><span>Photo indisponible</span></div>}
    {announce && <span className={status === "loading" ? "photo-loading-label" : "sr-only"} role="status">
      {status === "loading" ? "Chargement de la photo…" : status === "error" ? "Cette photo n’est plus accessible." : "Photo chargée."}
    </span>}
  </div>;
}
