import torch
from diffusers import AutoPipelineForText2Image
from engine.config import config


class ImageGenerator:
    def __init__(self):
        self.model_id    = config.IMAGE_MODEL_NAME
        self.pipeline    = None
        self._fail_count = 0   # consecutive generation failures
        self._disabled   = False  # permanently off for this session after too many OOM

    # ------------------------------------------------------------------
    # VRAM safety gate
    # ------------------------------------------------------------------

    def is_disabled(self):
        """True if auto-generation has been permanently disabled this session."""
        return self._disabled

    def can_generate_safely(self):
        """
        True if conditions allow an image generation attempt:
          - not permanently disabled
          - VRAM strategy is not A (skip)
          - GPU is available
          - free VRAM >= IMAGE_VRAM_REQUIRED_GB threshold

        Falls back to True when the CUDA memory API is unavailable (non-GPU env
        will be caught by the strategy check anyway).
        """
        if self._disabled:
            return False
        if config.VRAM_STRATEGY == "A":
            return False
        if not torch.cuda.is_available():
            return False
        try:
            free_bytes = torch.cuda.mem_get_info()[0]
            free_gb    = free_bytes / (1024 ** 3)
            required   = getattr(config, 'IMAGE_VRAM_REQUIRED_GB', 4.0)
            return free_gb >= required
        except Exception:
            return True  # assume OK if mem_get_info not supported

    def reset_disabled(self):
        """Re-enable generation (e.g. user clicks Retry in sidebar)."""
        self._disabled   = False
        self._fail_count = 0

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def load_model(self):
        if self.pipeline is None:
            print(f"[ImageGen] Loading {self.model_id} into VRAM…")
            self.pipeline = AutoPipelineForText2Image.from_pretrained(
                self.model_id, torch_dtype=torch.float16, variant="fp16"
            )
            self.pipeline.enable_model_cpu_offload()

    def unload_model(self):
        if self.pipeline is not None:
            print(f"[ImageGen] Unloading {self.model_id} from VRAM…")
            del self.pipeline
            torch.cuda.empty_cache()
            self.pipeline = None

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_image(self, prompt, context_type="scene"):
        """
        Generate an image from prompt.  Returns PIL Image or None.

        Handles:
          - Strategy A: always skip
          - Strategy B: load → generate (2 steps) → unload each call
          - OOM / exception: increment fail counter; disable after MAX_FAILURES
        """
        if config.VRAM_STRATEGY == "A":
            return None
        if self._disabled:
            return None

        if config.VRAM_STRATEGY == "B":
            try:
                self.load_model()
                image = self.pipeline(
                    prompt=prompt,
                    num_inference_steps=2,
                    guidance_scale=0.0,
                ).images[0]
                self.unload_model()
                self._fail_count = 0   # reset streak on success
                return image

            except torch.cuda.OutOfMemoryError:
                self.unload_model()
                self._fail_count += 1
                max_f = getattr(config, 'IMAGE_GEN_MAX_FAILURES', 3)
                if self._fail_count >= max_f:
                    self._disabled = True
                    print(f"[ImageGen] OOM × {self._fail_count} — auto-generation disabled.")
                else:
                    print(f"[ImageGen] OOM (failure {self._fail_count}/{max_f})")
                return None

            except Exception as exc:
                self.unload_model()
                self._fail_count += 1
                max_f = getattr(config, 'IMAGE_GEN_MAX_FAILURES', 3)
                if self._fail_count >= max_f:
                    self._disabled = True
                    print(f"[ImageGen] {exc} — disabled after {self._fail_count} failures.")
                else:
                    print(f"[ImageGen] {exc} (failure {self._fail_count}/{max_f})")
                return None

        print(f"[ImageGen] Unknown VRAM strategy '{config.VRAM_STRATEGY}' — skipping.")
        return None
