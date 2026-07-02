export const languages = { sv: 'Svenska', en: 'English' } as const;
export type Lang = keyof typeof languages;

export const ui = {
  sv: {
    'site.title': 'AI Riksdag',
    'site.tagline': 'Hur borde partierna ha röstat — enligt sina egna dokument?',
    'nav.cases': 'Ärenden',
    'nav.parties': 'Partier',
    'nav.method': 'Metod',
    'nav.data': 'Data',
    'home.intro':
      'För varje votering i riksdagen 2022–2026 avgör en AI-agent per parti hur partiet borde rösta — enbart utifrån partiets valmanifest, partiprogram och (för regeringssidan) Tidöavtalet, samt en tidsbunden lägesbild. Resultatet jämförs med hur partiet faktiskt röstade.',
    'home.disclaimer':
      'Detta är rekonstruktion, inte prediktion: modellens träningsdata innehåller sannolikt de verkliga utfallen. Kontamineringen mäts och redovisas öppet. Projektet är partipolitiskt obundet.',
    'home.agreement': 'Överensstämmelse AI ↔ verklig röst',
    'home.latest': 'Senaste ärenden',
    'home.noai': 'Simuleringen har inte körts ännu — statistik visas när resultaten finns.',
    'case.actual': 'Verklig röst',
    'case.ai': 'AI enligt partidokument',
    'case.committee': 'Utskottets förslag',
    'case.alternatives': 'Motförslag',
    'case.motivering': 'AI-motivering per parti',
    'case.probe': 'Minnesprob (kontaminering)',
    'case.probe.recalled': 'Modellen kunde återge det verkliga utfallet ur minnet — tolka överensstämmelsen försiktigt.',
    'case.probe.notRecalled': 'Modellen kunde inte återge det verkliga utfallet ur minnet.',
    'case.source': 'Källa: Sveriges riksdag',
    'case.match': 'lika',
    'case.mismatch': 'skiljer',
    'case.confidence': 'säkerhet',
    'case.coverage': 'täckning i dokumenten',
    'case.decision': 'Riksdagens beslut',
    'browser.title': 'Alla ärenden',
    'browser.search': 'Sök ärende…',
    'browser.filter.all': 'Alla',
    'browser.filter.disagree': 'AI ≠ verklig röst',
    'party.agreement': 'Överensstämmelse över tid',
    'party.confusion': 'AI-röst mot verklig röst',
    'party.support': 'Väljarstöd (opinionsmätningar)',
    'party.supportSource': 'Månadsgenomsnitt av publicerade mätningar (SwedishPolls-datasetet). Visas endast här som kontext — AI-agenterna får medvetet ingen opinionsdata: de ska följa partiets plan, inte anpassa den efter opinionen.',
    'party.title': 'Partisidor',
    'vote.Ja': 'Ja',
    'vote.Nej': 'Nej',
    'vote.Avstår': 'Avstår',
    'vote.Frånvarande': 'Frånvarande',
    'coverage.explicit': 'uttryckligt',
    'coverage.inferred': 'härlett',
    'coverage.not_covered': 'täcks ej',
    'footer.license': 'Öppen forskningsdata och kod (MIT).',
  },
  en: {
    'site.title': 'AI Riksdag',
    'site.tagline': 'How should the parties have voted — according to their own documents?',
    'nav.cases': 'Cases',
    'nav.parties': 'Parties',
    'nav.method': 'Methodology',
    'nav.data': 'Data',
    'home.intro':
      'For every chamber vote in the Swedish Riksdag 2022–2026, one AI agent per party decides how that party should vote — based solely on its election manifesto, party programme and (for the governing side) the Tidö agreement, plus a point-in-time snapshot of the country. The decision is compared with how the party actually voted.',
    'home.disclaimer':
      'This is reconstruction, not prediction: the model\'s training data likely contains the real outcomes. Contamination is measured and reported openly. The project is politically unaffiliated.',
    'home.agreement': 'Agreement AI ↔ actual vote',
    'home.latest': 'Latest cases',
    'home.noai': 'The simulation has not run yet — statistics appear once results exist.',
    'case.actual': 'Actual vote',
    'case.ai': 'AI per party documents',
    'case.committee': "Committee proposal",
    'case.alternatives': 'Counter-proposals',
    'case.motivering': 'AI reasoning per party',
    'case.probe': 'Memorization probe (contamination)',
    'case.probe.recalled': 'The model could recall the real outcome from memory — interpret agreement with caution.',
    'case.probe.notRecalled': 'The model could not recall the real outcome from memory.',
    'case.source': 'Source: The Swedish Parliament',
    'case.match': 'match',
    'case.mismatch': 'differs',
    'case.confidence': 'confidence',
    'case.coverage': 'document coverage',
    'case.decision': 'Riksdag decision',
    'browser.title': 'All cases',
    'browser.search': 'Search cases…',
    'browser.filter.all': 'All',
    'browser.filter.disagree': 'AI ≠ actual vote',
    'party.agreement': 'Agreement over time',
    'party.confusion': 'AI vote vs actual vote',
    'party.support': 'Polling support',
    'party.supportSource': 'Monthly average of published polls (SwedishPolls dataset). Shown here as context only — the AI agents deliberately receive no polling data: they must follow the party\'s plan, not adjust it to public opinion.',
    'party.title': 'Parties',
    'vote.Ja': 'Yes',
    'vote.Nej': 'No',
    'vote.Avstår': 'Abstain',
    'vote.Frånvarande': 'Absent',
    'coverage.explicit': 'explicit',
    'coverage.inferred': 'inferred',
    'coverage.not_covered': 'not covered',
    'footer.license': 'Open research data and code (MIT).',
  },
} as const;

export function useTranslations(lang: Lang) {
  return function t(key: keyof (typeof ui)['sv']): string {
    return ui[lang][key] ?? ui.sv[key];
  };
}

export function localePath(lang: Lang, path: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  return lang === 'sv' ? `${base}${path}` : `${base}/en${path}`;
}
