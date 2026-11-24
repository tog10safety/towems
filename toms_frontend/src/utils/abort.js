/**
 * How an abort order's end point reads wherever it is shown.
 *
 * "Last stop" is the stop the vehicles have actually reached; the plan's own
 * final stop is offered separately so an abort never quietly sends a convoy
 * further out than it already is.
 */
export const ABORT_DESTINATIONS = [
  { value: "station", label: "Return to station", phrase: "return to station" },
  {
    value: "last_stop",
    label: "Last stop already reached",
    phrase: "proceed to the last stop you have reached",
  },
  {
    value: "final_stop",
    label: "Planned final stop",
    phrase: "continue to the planned final stop",
  },
];

const SHORT = {
  station: "Station",
  last_stop: "Last stop reached",
  final_stop: "Final stop",
};

function findDestination(destination) {
  return ABORT_DESTINATIONS.find((row) => row.value === destination) || ABORT_DESTINATIONS[0];
}

export function abortPhrase(destination) {
  return findDestination(destination).phrase;
}

export function abortLabel(destination) {
  return findDestination(destination).label;
}

export function abortShort(destination) {
  return SHORT[destination] || SHORT.station;
}
