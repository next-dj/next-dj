const CHOICES = {
  accept: (consent) => consent.acceptAll(),
  analytics: (consent) => consent.update({ analytics: true, marketing: false }),
  reject: (consent) => consent.rejectAll(),
};

const banner = document.querySelector("[data-consent-banner]");

async function driveBanner() {
  const { consent } = await window.Next.ready("scripts");
  banner.hidden = consent.decided();
  window.Next.on("next:consent", () => {
    banner.hidden = consent.decided();
  });
  banner.addEventListener("click", (event) => {
    const button = event.target.closest("[data-consent-choice]");
    if (!button) {
      return;
    }
    CHOICES[button.dataset.consentChoice](consent);
    banner.hidden = true;
  });
}

if (banner) {
  driveBanner();
}
