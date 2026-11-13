import { useEffect, useMemo, useRef, useState } from "react";
import {
  currentTimeManila,
  formatDateTime,
  formatTime,
  normalizeTimeInput,
  todayManila,
} from "../utils/format.js";
import Icon from "./Icon.jsx";
import OperationalLocationPickerModal from "./OperationalLocationPickerModal.jsx";
import { Button, DateTimeInput, Field, Input, Select } from "./ui.jsx";
import { useApi } from "../hooks/useApi.js";
import { api } from "../api/client.js";
import { addMinutes } from "../utils/airEta.js";
import { itineraryRoute } from "../utils/airRoute.js";

const AIR_LEG_EXCLUDED_FEATURE_TYPES = ["frequent_location"];

function legIsLocked(leg) {
  return Boolean(
    leg?.atd ||
      leg?.ata ||
      leg?.tot ||
      leg?.toot ||
      leg?.status === "cancelled"
  );
}

const AIRCRAFT_FORMATIONS = [
  { count: 1, label: "1 · Single" },
  { count: 2, label: "2 · Element" },
  { count: 3, label: "3 · Three-ship" },
  { count: 4, label: "4 · Flight" },
  { count: 5, label: "5 · Five-ship" },
  { count: 6, label: "6 · Six-ship" },
  { count: 7, label: "7 · Seven-ship" },
  { count: 8, label: "8 · Division" },
  { count: 12, label: "12 · Squadron-sized formation" },
];

function splitOperationTime(value) {
  const [hour = "", minute = ""] = String(value || "").split(":");
  return { hour, minute };
}

function emptyLeg() {
  return {
    eta: "",
    origin: "",
    destination: "",
    route: "",
    mission: "",
    point_role: "",
    overflight: false,
    return_to_origin: false,
  };
}

function emptyAircraft(isLead = false) {
  return {
    aircraft_type: "",
    tail_number: "",
    callsign: "",
    is_lead: isLead,
    crew: [
      { role: "PIC", name: "" },
      { role: "CP", name: "" },
    ],
    itineraries: [emptyLeg()],
  };
}

function emptyElement() {
  return { mission: "", aircraft: [emptyAircraft(true)] };
}

/** Add one minute to a wall-clock input value, rolling over the day safely. */
function addMinute(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(
    String(value || "")
  );
  if (!match) return "";
  const [, year, month, day, hour, minute] = match.map(Number);
  const next = new Date(Date.UTC(year, month - 1, day, hour, minute + 1));
  const pad = (part) => String(part).padStart(2, "0");
  return `${next.getUTCFullYear()}-${pad(next.getUTCMonth() + 1)}-${pad(
    next.getUTCDate()
  )}T${pad(next.getUTCHours())}:${pad(next.getUTCMinutes())}`;
}

function crewValue(aircraft, role) {
  return (aircraft.crew || []).find((row) => row.role === role)?.name || "";
}

function extraCrew(aircraft) {
  return (aircraft.crew || [])
    .map((row, index) => ({ ...row, index }))
    .filter((row) => !["PIC", "CP"].includes(row.role));
}

function chainedLegs(legs) {
  return legs.map((leg, index) =>
    index === 0
      ? leg
      : { ...leg, origin: legs[index - 1]?.destination || "" }
  );
}

function compactLocation(parts) {
  const values = parts.map((part) => (part || "").trim()).filter(Boolean);
  return values
    .filter(
      (part, index) =>
        values.findIndex(
          (item) => item.toLowerCase() === part.toLowerCase()
        ) === index
    )
    .join(", ");
}

function pickedLocationLine(picked) {
  const detail = picked.detail || {};
  return compactLocation([
    detail.label || picked.location_name,
    detail.address,
    detail.mgrs || picked.mgrs,
  ]);
}

export default function AirFlightOperationsFields({
  air,
  onChange,
  dependsOn,
  dependencyRelease,
  dependencyBuffer,
  onDependencyChange,
  onChooseDependency,
  dependencyOptions = [],
  dependencyError = "",
  airAssets = [],
  airAssetConflicts = [],
  airConflictsLoading = false,
  airConflictsError = "",
  detailsLocked = false,
}) {
  const elements = air.elements?.length ? air.elements : [emptyElement()];
  const latestAirRef = useRef(air);
  latestAirRef.current = air;
  const formationSignature = elements
    .map((element) => {
      const lead =
        element.aircraft.find((aircraft) => aircraft.is_lead) ||
        element.aircraft[0];
      const asset = lead
        ? airAssetFor(lead.aircraft_type, lead.tail_number)
        : null;
      return [
        element.aircraft.length,
        lead?.aircraft_type || "",
        lead?.tail_number || "",
        lead?.is_lead ? "lead" : "",
        asset?.callsign || "",
        asset?.tail_number || "",
      ].join(":");
    })
    .join("|");
  const minimumDate = todayManila();
  // Saved operational locations are also offered as the leg field's
  // suggestions, so a planner can type instead of opening the map.
  const operationalFeatures = useApi("/operational-mapping/?page_size=500");
  const unitMarkers = useApi("/units/markers/");
  const airfields = useApi("/airfields/?page_size=500");
  const locationSuggestions = useMemo(() => {
    const rows = [];
    const seen = new Set();
    const add = (...parts) => {
      const value = compactLocation(parts);
      if (!value || seen.has(value)) return;
      seen.add(value);
      rows.push(value);
    };
    for (const feature of operationalFeatures.data?.results ?? []) {
      add(feature.acronym || feature.name, feature.address, feature.mgrs);
    }
    for (const unit of unitMarkers.data?.results ?? []) {
      add(unit.unit_acronym || unit.unit_name, unit.place_name, unit.mgrs);
    }
    for (const field of airfields.data?.results ?? []) {
      add(field.code || field.name, field.location, field.grid_coordinate);
    }
    return rows.sort((left, right) => left.localeCompare(right));
  }, [operationalFeatures.data, unitMarkers.data, airfields.data]);
  const { hour: operationHour, minute: operationMinute } = splitOperationTime(
    air.operation_time
  );
  const operationTime = normalizeTimeInput(air.operation_time);
  const missionEtd =
    air.operation_date && operationTime
      ? `${air.operation_date}T${operationTime}`
      : "";
  const earliestEta = addMinute(missionEtd);
  // The Air Assets endpoint is already scoped to the caller's current unit and
  // its subordinate units. Collapse those airframes into aircraft-type choices.
  const unitAirAssetTypes = Array.from(
    new Set(
      (airAssets || [])
        .map((asset) => asset.model)
        .filter(Boolean)
    )
  ).sort((left, right) => left.localeCompare(right));
  const selectedDependency = (dependencyOptions || []).find(
    (row) => String(row.id) === String(dependsOn)
  );
  const dependencyArrival =
    selectedDependency?.air_detail?.rendezvous_ready_at ||
    selectedDependency?.air_detail?.actual_arrival ||
    selectedDependency?.air_detail?.scheduled_arrival ||
    null;

  const conflictByAsset = new Map(
    (airAssetConflicts || [])
      .filter((row) => row.air_asset !== null && row.air_asset !== undefined)
      .map((row) => [String(row.air_asset), row])
  );

  function currentLocationLabel(asset) {
    if (!asset) return "location not recorded";
    const location = (asset.current_location || "").trim();
    if (!location) return "location not recorded";
    return asset.map_location?.mgrs ? location : `${location} (no MGRS)`;
  }

  function aircraftAvailability(asset) {
    const reasons = [];
    if (asset.operational_status && asset.operational_status !== "available") {
      reasons.push(
        asset.operational_status_label ||
          asset.operational_status.replace(/_/g, " ")
      );
    }
    const conflict = conflictByAsset.get(String(asset.id));
    if (conflict) {
      const window =
        conflict.start_at && conflict.end_at
          ? ` (${formatTime(conflict.start_at)}–${formatTime(conflict.end_at)})`
          : "";
      reasons.push(
        `already assigned to ${conflict.mission_number || "another mission"}${window}`
      );
    }
    return { available: reasons.length === 0, reason: reasons.join("; ") };
  }

  function selectedTailsElsewhere(elementIndex, aircraftIndex) {
    const tails = new Set();
    elements.forEach((element, position) => {
      (element.aircraft || []).forEach((aircraft, rowIndex) => {
        if (position === elementIndex && rowIndex === aircraftIndex) return;
        const tailNumber = (aircraft.tail_number || "").trim();
        if (tailNumber) tails.add(tailNumber);
      });
    });
    return tails;
  }

  function tailOptionsForType(type, excludedTails = new Set()) {
    if (!type) return [];
    const seen = new Set();
    return (airAssets || [])
      .filter((asset) => asset.model === type)
      .map((asset) => {
        const tailNumber = (asset.tail_number || "").trim();
        if (!tailNumber || seen.has(tailNumber) || excludedTails.has(tailNumber)) {
          return null;
        }
        seen.add(tailNumber);
        return { tailNumber, asset, ...aircraftAvailability(asset) };
      })
      .filter(Boolean)
      .sort((left, right) => left.tailNumber.localeCompare(right.tailNumber));
  }

  function typeOptions(excludedTails = new Set()) {
    const rows = unitAirAssetTypes.map((type) => {
      const allTails = tailOptionsForType(type);
      const tails = tailOptionsForType(type, excludedTails);
      const availableCount = tails.filter((row) => row.available).length;
      return {
        type,
        availableCount,
        available: availableCount > 0,
        reason:
          availableCount > 0
            ? ""
            : tails.length
              ? "no aircraft available"
              : allTails.length
                ? "all aircraft already selected on this sheet"
                : "no aircraft in the register",
      };
    });
    const selectedTypes = new Set(
      elements.flatMap((element) =>
        (element.aircraft || []).map((aircraft) => aircraft.aircraft_type)
      )
    );
    for (const type of selectedTypes) {
      if (!type || rows.some((row) => row.type === type)) continue;
      rows.push({
        type,
        availableCount: 0,
        available: false,
        reason: "not in the Air Assets register",
      });
    }
    return rows;
  }

  function airAssetFor(type, tailNumber) {
    if (!type || !tailNumber) return null;
    return (
      (airAssets || []).find(
        (asset) =>
          asset.model === type && asset.tail_number === tailNumber
      ) || null
    );
  }

  // A formation flies at its slowest aircraft's cruising speed. If any aircraft
  // has no cruising speed in the Air Assets register, automatic ETA is skipped
  // rather than using an optimistic number.
  function elementEtaSpeedKnots(element) {
    const planes = element.aircraft || [];
    if (!planes.length) return 0;
    const speeds = planes.map((aircraft) => {
      const speed = Number(
        airAssetFor(aircraft.aircraft_type, aircraft.tail_number)
          ?.cruising_speed_knots
      );
      return Number.isFinite(speed) && speed > 0 ? speed : 0;
    });
    if (speeds.some((speed) => !speed)) return 0;
    return Math.min(...speeds);
  }

  function withLeadOrigin(element, rows) {
    const lead = rows.find((row) => row.is_lead) || rows[0];
    const asset = lead
      ? airAssetFor(lead.aircraft_type, lead.tail_number)
      : null;
    const origin = (asset?.current_location || "").trim();
    const fallbackLegs = element.aircraft?.[0]?.itineraries?.length
      ? element.aircraft[0].itineraries
      : [emptyLeg()];
    return {
      aircraft: rows.map((aircraft) => {
        const legs = aircraft.itineraries?.length
          ? aircraft.itineraries
          : fallbackLegs;
        return {
          ...aircraft,
          itineraries: legs.map((leg, legIndex) =>
            legIndex === 0 ? { ...leg, origin } : leg
          ),
        };
      }),
    };
  }

  function callsignBase(value) {
    return String(value || "")
      .replace(/\s+flight$/i, "")
      .trim();
  }

  function callsignWithoutTail(value) {
    return callsignBase(value)
      .replace(/[-\s]+\d{1,6}$/, "")
      .trim();
  }

  function flightCallsign(base) {
    const text = String(base || "").trim();
    if (!text) return "";
    return /\s+flight$/i.test(text) ? text : `${text} Flight`;
  }

  function formationCallsign(value) {
    const base = callsignWithoutTail(value);
    return base ? flightCallsign(base) : "";
  }

  function callsignForRows(rows, fallbackToCurrent = false) {
    const lead = rows.find((row) => row.is_lead) || rows[0];
    if (!lead) return "";
    const asset = airAssetFor(lead.aircraft_type, lead.tail_number);
    const base = asset
      ? (asset.callsign || asset.tail_number || "").trim()
      : fallbackToCurrent
        ? callsignBase(lead.callsign)
        : "";
    if (!base) return "";
    if (rows.length < 2) return base;
    // A formation uses the lead aircraft's call sign without its tail number,
    // e.g. "Hornet 001" becomes "Hornet Flight".
    return formationCallsign(base);
  }

  function patchAircraftIdentity(elementIndex, aircraftIndex, next) {
    const element = elements[elementIndex];
    const rows = element.aircraft.map((aircraft, position) =>
      position === aircraftIndex ? { ...aircraft, ...next } : aircraft
    );
    const hasLead = element.aircraft.some((aircraft) => aircraft.is_lead);
    const isLead = hasLead
      ? Boolean(element.aircraft[aircraftIndex]?.is_lead)
      : aircraftIndex === 0;
    const nextRows = isLead
      ? (() => {
          const callsign = callsignForRows(rows, false);
          return rows.map((aircraft) => ({ ...aircraft, callsign }));
        })()
      : rows;
    patchElement(elementIndex, withLeadOrigin(element, nextRows));
  }

  function changeOperationTime(part, value) {
    const digits = String(value || "")
      .replace(/\D/g, "")
      .slice(0, 2);
    const hour = part === "hour" ? digits : operationHour;
    const minute = part === "minute" ? digits : operationMinute;
    if (hour && Number(hour) > 23) return;
    if (minute && Number(minute) > 59) return;
    if (hour && minute) {
      const normalized = normalizeTimeInput(`${hour}:${minute}`);
      if (!normalized) return;
      const currentMinimum =
        air.operation_date === minimumDate ? currentTimeManila() : "";
      if (currentMinimum && normalized < currentMinimum) return;
    }
    patch({
      operation_time: hour || minute ? `${hour}:${minute}` : "",
    });
  }

  function tidyOperationTime() {
    const hour = operationHour ? operationHour.padStart(2, "0") : "";
    const minute = operationMinute ? operationMinute.padStart(2, "0") : "";
    if (!hour && !minute) return;
    if (Number(hour) > 23 || Number(minute) > 59) return;
    const normalized = `${hour}:${minute}`;
    if (hour && minute) {
      const currentMinimum =
        air.operation_date === minimumDate ? currentTimeManila() : "";
      if (currentMinimum && normalized < currentMinimum) return;
    }
    if (normalized !== air.operation_time) {
      patch({ operation_time: normalized });
    }
  }

  const [dragAircraft, setDragAircraft] = useState(null);
  const [dragOver, setDragOver] = useState(null);
  const [legLocationTarget, setLegLocationTarget] = useState(null);
  const [etaBusy, setEtaBusy] = useState(false);
  const [etaError, setEtaError] = useState("");
  const autoEtaRef = useRef({});

  const autoEtaKey = useMemo(
    () =>
      JSON.stringify({
        etd: missionEtd,
        elements: elements.map((element) => ({
          speed: elementEtaSpeedKnots(element),
          legs: chainedLegs(sharedLegs(element)).map((leg) => ({
            origin: leg.origin || "",
            destination: leg.destination || "",
            locked: legIsLocked(leg),
          })),
        })),
      }),
    [elements, missionEtd, airAssets]
  );

  function patch(next) {
    onChange({ ...air, ...next });
  }

  // Fill each leg's ETA from the mission ETD, the leg's MGRS distance and the
  // selected aircraft's cruising speed. A leg whose ETA was typed by the
  // planner is left alone; an ETA this effect previously filled is refreshed
  // when the route or aircraft changes.
  useEffect(() => {
    if (detailsLocked || !missionEtd) {
      setEtaBusy(false);
      setEtaError("");
      return undefined;
    }
    const plans = elements
      .map((element, elementIndex) => {
        const speed = elementEtaSpeedKnots(element);
        const legs = chainedLegs(sharedLegs(element));
        if (!speed || !legs.length) return null;
        if (!legs.some((leg) => leg.origin && leg.destination)) return null;
        return { elementIndex, speed, legs };
      })
      .filter(Boolean);
    if (!plans.length) {
      setEtaBusy(false);
      setEtaError("");
      return undefined;
    }

    let cancelled = false;
    setEtaError("");
    const timer = setTimeout(async () => {
      setEtaBusy(true);
      try {
        const etaUpdates = {};
        for (const plan of plans) {
          const response = await api.post("/gis/air-eta/", {
            cruising_speed_knots: plan.speed,
            legs: plan.legs.map((leg) => ({
              origin: leg.origin || "",
              destination: leg.destination || "",
            })),
          });
          if (cancelled) return;

          let cursor = missionEtd;
          etaUpdates[plan.elementIndex] = plan.legs.map((leg, legIndex) => {
            if (legIsLocked(leg)) {
              if (leg.eta) cursor = leg.eta;
              return leg.eta || "";
            }
            const row = response.legs?.[legIndex];
            const key = `${plan.elementIndex}:${legIndex}`;
            const previousAuto = autoEtaRef.current[key];
            let next = leg.eta || "";
            if (!row?.resolved) {
              // Do not leave an automatically calculated ETA attached to a
              // route whose MGRS can no longer be resolved.
              if (next && next === previousAuto) {
                next = "";
                delete autoEtaRef.current[key];
              }
            } else if (cursor) {
              const candidate = addMinutes(
                cursor,
                Math.max(1, Math.round(row.duration_minutes))
              );
              if (!next || next === previousAuto) {
                next = candidate;
                autoEtaRef.current[key] = candidate;
              }
            }
            if (next) cursor = next;
            return next;
          });
        }
        if (cancelled) return;

        const latest = latestAirRef.current;
        const latestElements = latest.elements?.length
          ? latest.elements
          : [emptyElement()];
        let changed = false;
        const nextElements = latestElements.map((element, elementIndex) => {
          const etas = etaUpdates[elementIndex];
          const plan = plans.find((row) => row.elementIndex === elementIndex);
          if (!etas) return element;
          const baseLegs = sharedLegs(element);
          let elementChanged = false;
          const nextLegs = baseLegs.map((leg, legIndex) => {
            const currentEta = leg.eta || "";
            const originalEta = plan?.legs[legIndex]?.eta || "";
            const previousAuto = autoEtaRef.current[`${elementIndex}:${legIndex}`];
            const canApply =
              !currentEta ||
              currentEta === originalEta ||
              currentEta === previousAuto;
            if (!canApply) return leg;
            const eta = etas[legIndex] ?? leg.eta;
            if (eta === currentEta) return leg;
            elementChanged = true;
            return { ...leg, eta };
          });
          if (!elementChanged) return element;
          changed = true;
          return {
            ...element,
            aircraft: element.aircraft.map((aircraft) => ({
              ...aircraft,
              itineraries: nextLegs.map((leg) => ({ ...leg })),
            })),
          };
        });
        if (changed) onChange({ ...latest, elements: nextElements });
      } catch (err) {
        if (!cancelled) {
          setEtaError(err.message || "Automatic ETA could not be calculated.");
        }
      } finally {
        if (!cancelled) setEtaBusy(false);
      }
    }, 400);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // autoEtaKey already contains the route, ETD and speed inputs; rebuilding
    // the callback whenever an ETA changes would overwrite a manual override.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoEtaKey, detailsLocked]);

  // A new sheet dated today starts at the current Philippine hour and minute.
  // Read the clock when this form mounts, not when the module was first loaded.
  useEffect(() => {
    if (air.operation_date === minimumDate && !air.operation_time) {
      patch({ operation_time: currentTimeManila() });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Normalize a saved formation's call sign once per formation/lead identity.
  // The signature excludes the current call sign so this never fights typing.
  useEffect(() => {
    let changed = false;
    const next = elements.map((element) => {
      if (element.aircraft.length < 2) return element;
      const callsign = callsignForRows(element.aircraft, true);
      if (!callsign) return element;
      if (element.aircraft.every((aircraft) => aircraft.callsign === callsign)) {
        return element;
      }
      changed = true;
      return {
        ...element,
        aircraft: element.aircraft.map((aircraft) => ({
          ...aircraft,
          callsign,
        })),
      };
    });
    if (changed) onChange({ ...air, elements: next });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formationSignature]);

  function patchElement(index, next) {
    patch({
      elements: elements.map((element, position) =>
        position === index ? { ...element, ...next } : element
      ),
    });
  }

  function patchAircraft(elementIndex, aircraftIndex, next) {
    patchElement(elementIndex, {
      aircraft: elements[elementIndex].aircraft.map((aircraft, position) =>
        position === aircraftIndex ? { ...aircraft, ...next } : aircraft
      ),
    });
  }

  function sharedLegs(element) {
    return element.aircraft?.[0]?.itineraries || [];
  }

  function setSharedLegs(elementIndex, legs) {
    const element = elements[elementIndex];
    patchElement(elementIndex, {
      aircraft: element.aircraft.map((aircraft) => ({
        ...aircraft,
        itineraries: legs.map((leg) => ({ ...leg })),
      })),
    });
  }

  function patchLeg(elementIndex, legIndex, next) {
    const legs = chainedLegs(sharedLegs(elements[elementIndex])).map(
      (leg, position) =>
        position === legIndex ? { ...leg, ...next } : leg
    );
    if (next.destination !== undefined && legs[legIndex + 1]) {
      legs[legIndex + 1] = {
        ...legs[legIndex + 1],
        origin: next.destination || "",
      };
    }
    setSharedLegs(elementIndex, legs);
  }

  function applyLegLocation(picked) {
    if (!legLocationTarget) return;
    const value = pickedLocationLine(picked);
    patchLeg(legLocationTarget.elementIndex, legLocationTarget.legIndex, {
      [legLocationTarget.field]: value,
    });
    setLegLocationTarget(null);
  }

  function addLeg(elementIndex) {
    const legs = chainedLegs(sharedLegs(elements[elementIndex]));
    setSharedLegs(elementIndex, [
      ...legs,
      {
        ...emptyLeg(),
        origin: legs[legs.length - 1]?.destination || "",
      },
    ]);
  }

  function removeLeg(elementIndex, legIndex) {
    const legs = chainedLegs(sharedLegs(elements[elementIndex]));
    if (legs.length === 1) return;
    const next = legs.filter((_, position) => position !== legIndex);
    setSharedLegs(elementIndex, chainedLegs(next));
  }

  function setElementCallsign(elementIndex, value) {
    const element = elements[elementIndex];
    patchElement(elementIndex, {
      aircraft: element.aircraft.map((aircraft) => ({
        ...aircraft,
        callsign: value,
      })),
    });
  }

  function setAircraftCount(elementIndex, count) {
    const element = elements[elementIndex];
    const template = element.aircraft[0];
    const rows = [...element.aircraft];
    while (rows.length < count) {
      const added = emptyAircraft(false);
      added.callsign = template?.callsign || "";
      added.itineraries = (template?.itineraries || [emptyLeg()]).map((leg) => ({
        ...leg,
      }));
      rows.push(added);
    }
    const next = rows.slice(0, count).map((aircraft) => ({ ...aircraft }));
    if (count === 1) next[0].is_lead = true;
    if (count > 1 && !next.some((aircraft) => aircraft.is_lead)) {
      next[0].is_lead = true;
    }
    const callsign = callsignForRows(next, true);
    patchElement(
      elementIndex,
      withLeadOrigin(
        element,
        next.map((aircraft) => ({ ...aircraft, callsign }))
      )
    );
  }

  function removeAircraft(elementIndex, aircraftIndex) {
    const element = elements[elementIndex];
    if (element.aircraft.length === 1) return;
    const next = element.aircraft.filter((_, position) => position !== aircraftIndex);
    if (!next.some((aircraft) => aircraft.is_lead)) next[0].is_lead = true;
    const callsign = callsignForRows(next, true);
    patchElement(
      elementIndex,
      withLeadOrigin(
        element,
        next.map((aircraft) => ({ ...aircraft, callsign }))
      )
    );
  }

  function moveAircraftTo(elementIndex, fromIndex, toIndex) {
    if (fromIndex === toIndex) return;
    const element = elements[elementIndex];
    if (
      fromIndex < 0 ||
      fromIndex >= element.aircraft.length ||
      toIndex < 0 ||
      toIndex >= element.aircraft.length
    ) {
      return;
    }
    const next = [...element.aircraft];
    const [moved] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, moved);
    patchElement(elementIndex, withLeadOrigin(element, next));
  }

  function setAircraftRole(elementIndex, aircraftIndex, role) {
    const element = elements[elementIndex];
    const next = element.aircraft.map((aircraft, position) => ({
      ...aircraft,
      is_lead:
        role === "element_lead"
          ? position === aircraftIndex
          : position === aircraftIndex
            ? false
            : aircraft.is_lead,
    }));
    if (!next.some((aircraft) => aircraft.is_lead)) next[0].is_lead = true;
    const callsign = callsignForRows(next, false);
    patchElement(
      elementIndex,
      withLeadOrigin(
        element,
        next.map((aircraft) => ({ ...aircraft, callsign }))
      )
    );
  }

  function setCrewName(elementIndex, aircraftIndex, role, name) {
    const aircraft = elements[elementIndex].aircraft[aircraftIndex];
    const crew = [...(aircraft.crew || [])];
    const position = crew.findIndex((row) => row.role === role);
    if (position === -1) {
      crew.push({ role, name });
    } else {
      crew[position] = { ...crew[position], name };
    }
    patchAircraft(elementIndex, aircraftIndex, { crew });
  }

  function addCrew(elementIndex, aircraftIndex) {
    const aircraft = elements[elementIndex].aircraft[aircraftIndex];
    patchAircraft(elementIndex, aircraftIndex, {
      crew: [...(aircraft.crew || []), { role: "IP", name: "" }],
    });
  }

  function updateCrew(elementIndex, aircraftIndex, crewIndex, next) {
    const aircraft = elements[elementIndex].aircraft[aircraftIndex];
    patchAircraft(elementIndex, aircraftIndex, {
      crew: aircraft.crew.map((row, position) =>
        position === crewIndex ? { ...row, ...next } : row
      ),
    });
  }

  function removeCrew(elementIndex, aircraftIndex, crewIndex) {
    const aircraft = elements[elementIndex].aircraft[aircraftIndex];
    patchAircraft(elementIndex, aircraftIndex, {
      crew: aircraft.crew.filter((_, position) => position !== crewIndex),
    });
  }

  return (
    <>
    <div className="form-section">
      <h3>Flight Operations sheet</h3>
      <p className="hint">
        Set the operations date and mission ETD in 24-hour military time. Each NR is
        an element; select its aircraft formation, enter the shared mission and callsign,
        then plot the shared itineraries. ETAs are calculated from the aircraft cruising
        speed and the MGRS route distance; a typed ETA is kept as a manual override.
        The last leg's ETA is the mission's scheduled arrival. Overflight itineraries
        do not record ATD/ATA.
      </p>
      {etaBusy && (
        <p className="hint" role="status">
          Calculating ETAs from aircraft cruising speed…
        </p>
      )}
      {etaError && <p className="field-message">{etaError}</p>}
      {detailsLocked && (
        <div className="alert alert-warning">
          Actual flight times exist. Only unflown itinerary legs can be changed;
          the flight schedule, formation, aircraft, crew and callsign are locked.
        </div>
      )}

      <div className="air-schedule-row">
        <Field label="Operations date" required className="air-schedule-date">
          <Input
            type="date"
            min={minimumDate}
            disabled={detailsLocked}
            value={air.operation_date || ""}
            onChange={(event) => {
              const nextDate = event.target.value;
              const currentMinimum =
                nextDate === minimumDate ? currentTimeManila() : "";
              const normalizedTime = normalizeTimeInput(air.operation_time);
              patch({
                operation_date: nextDate,
                operation_time:
                  currentMinimum &&
                  (!normalizedTime || normalizedTime < currentMinimum)
                    ? currentMinimum
                    : air.operation_time,
              });
            }}
          />
        </Field>
        <Field label="ETD (24H Military PST)" className="air-schedule-time">
          <div className="air-time-row">
            <div className="time-parts" role="group" aria-label="ETD (PST, 24-hour)">
              <Input
                type="number"
                inputMode="numeric"
                autoComplete="off"
                min={0}
                max={23}
                step={1}
                disabled={detailsLocked}
                placeholder="HH"
                aria-label="ETD hour, 00 to 23"
                value={operationHour}
                onChange={(event) =>
                  changeOperationTime("hour", event.target.value)
                }
                onBlur={tidyOperationTime}
              />
              <span className="time-colon" aria-hidden="true">
                :
              </span>
              <Input
                type="number"
                inputMode="numeric"
                autoComplete="off"
                min={0}
                max={59}
                step={1}
                disabled={detailsLocked}
                placeholder="MM"
                aria-label="ETD minute, 00 to 59"
                value={operationMinute}
                onChange={(event) =>
                  changeOperationTime("minute", event.target.value)
                }
                onBlur={tidyOperationTime}
              />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={detailsLocked}
              title="Use the current Philippine time"
              onClick={() => patch({ operation_time: currentTimeManila() })}
            >
              Now
            </Button>
          </div>
        </Field>
      </div>

      {airConflictsError && (
        <p className="field-message">
          Aircraft availability could not be checked: {airConflictsError}
        </p>
      )}

      {elements.map((element, elementIndex) => {
        const legs = chainedLegs(sharedLegs(element));
        const callsign = element.aircraft?.[0]?.callsign || "";
        const currentCount = element.aircraft.length;
        // Keep an existing non-standard count selectable instead of showing a
        // blank control for a record created before these formation names.
        const formationOptions = AIRCRAFT_FORMATIONS.some(
          (formation) => formation.count === currentCount
        )
          ? AIRCRAFT_FORMATIONS
          : [
              ...AIRCRAFT_FORMATIONS,
              { count: currentCount, label: `${currentCount} · Current formation` },
            ];
        return (
          <div className="air-element-card" key={`air-element-${elementIndex}`}>
            <div className="air-element-head">
              <label className="air-count-field">
                <span>Aircraft Formation</span>
                <Select
                  value={currentCount}
                  disabled={detailsLocked}
                  onChange={(event) =>
                    setAircraftCount(elementIndex, Number(event.target.value))
                  }
                >
                  {formationOptions.map((formation) => (
                    <option key={formation.count} value={formation.count}>
                      {formation.label}
                    </option>
                  ))}
                </Select>
              </label>
            </div>

            <label className="air-inline-label air-mission-row">
              <span>Mission</span>
              <Input
                value={element.mission || ""}
                disabled={detailsLocked}
                onChange={(event) =>
                  patchElement(elementIndex, { mission: event.target.value })
                }
                placeholder="Troop insertion"
              />
            </label>

            {element.aircraft.map((aircraft, aircraftIndex) => {
              const selectedElsewhere = selectedTailsElsewhere(
                elementIndex,
                aircraftIndex
              );
              const airTypeOptions = typeOptions(selectedElsewhere);
              const tailOptions = tailOptionsForType(
                aircraft.aircraft_type,
                selectedElsewhere
              );
              const assignedTailOption = tailOptions.find(
                (option) => option.tailNumber === aircraft.tail_number
              );
              const fallbackTailOption =
                aircraft.tail_number && !assignedTailOption
                  ? {
                      tailNumber: aircraft.tail_number,
                      asset: null,
                      available: false,
                      reason: selectedElsewhere.has(aircraft.tail_number)
                        ? "already selected on another aircraft"
                        : "not in the Air Assets register",
                    }
                  : null;
              const selectedTailOption = assignedTailOption || fallbackTailOption;
              const visibleTailOptions = fallbackTailOption
                ? [...tailOptions, fallbackTailOption]
                : tailOptions;
              const availableTailCount = tailOptions.filter(
                (option) => option.available
              ).length;
              return (
              <div
                className={`air-aircraft-block${
                  element.aircraft.length > 1 ? " is-formation" : ""
                }${
                  dragAircraft?.elementIndex === elementIndex &&
                  dragAircraft.aircraftIndex === aircraftIndex
                    ? " is-dragging"
                    : ""
                }${
                  dragOver?.elementIndex === elementIndex &&
                  dragOver.aircraftIndex === aircraftIndex
                    ? " is-drag-over"
                    : ""
                }`}
                key={`aircraft-${elementIndex}-${aircraftIndex}`}
                onDragOver={(event) => {
                  if (dragAircraft?.elementIndex !== elementIndex) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  setDragOver({ elementIndex, aircraftIndex });
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) {
                    setDragOver((current) =>
                      current?.elementIndex === elementIndex &&
                      current?.aircraftIndex === aircraftIndex
                        ? null
                        : current
                    );
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  if (dragAircraft?.elementIndex === elementIndex) {
                    moveAircraftTo(
                      elementIndex,
                      dragAircraft.aircraftIndex,
                      aircraftIndex
                    );
                  }
                  setDragAircraft(null);
                  setDragOver(null);
                }}
              >
                <div className="air-aircraft-row">
                  <span
                    className="air-drag-handle"
                    draggable={!detailsLocked}
                    role="button"
                    tabIndex={0}
                    title="Drag to reorder aircraft"
                    aria-label={`Drag aircraft ${aircraftIndex + 1} to reorder`}
                    onDragStart={(event) => {
                      setDragAircraft({ elementIndex, aircraftIndex });
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData(
                        "text/plain",
                        String(aircraftIndex)
                      );
                    }}
                    onDragEnd={() => {
                      setDragAircraft(null);
                      setDragOver(null);
                    }}
                  >
                    ⠿
                  </span>
                  <strong className="air-aircraft-title">
                    ACFT #{aircraftIndex + 1}
                  </strong>
                  <Select
                    className="air-aircraft-type"
                    aria-label={`Aircraft ${aircraftIndex + 1} type`}
                    title="Aircraft types from Air Assets controlled by your unit and its subordinate units. Types with no available aircraft are disabled."
                    value={aircraft.aircraft_type || ""}
                    disabled={detailsLocked}
                    onChange={(event) => {
                      const nextType = event.target.value;
                      const nextTail = tailOptionsForType(
                        nextType,
                        selectedElsewhere
                      ).some(
                        (option) =>
                          option.tailNumber === aircraft.tail_number &&
                          option.available
                      )
                        ? aircraft.tail_number
                        : "";
                      patchAircraftIdentity(elementIndex, aircraftIndex, {
                        aircraft_type: nextType,
                        tail_number: nextTail,
                      });
                    }}
                    style={{ flex: "1 1 130px" }}
                  >
                    <option value="">
                      {airTypeOptions.length
                        ? "Select aircraft type"
                        : "No aircraft types available"}
                    </option>
                    {airTypeOptions.map((option) => (
                      <option
                        key={option.type}
                        value={option.type}
                        disabled={
                          !option.available &&
                          option.type !== aircraft.aircraft_type
                        }
                      >
                        {option.type}
                        {option.available ? "" : ` — ${option.reason}`}
                      </option>
                    ))}
                  </Select>
                  <Select
                    className="air-aircraft-tail"
                    aria-label={`Aircraft ${aircraftIndex + 1} tail number`}
                    title={
                      airConflictsLoading
                        ? "Checking aircraft availability for this flight window…"
                        : "Each option shows the aircraft's recorded current location. Only aircraft available for this flight window can be selected."
                    }
                    value={aircraft.tail_number || ""}
                    disabled={detailsLocked || !aircraft.aircraft_type}
                    onChange={(event) =>
                      patchAircraftIdentity(elementIndex, aircraftIndex, {
                        tail_number: event.target.value,
                      })
                    }
                    style={{ flex: "1 1 240px" }}
                  >
                    <option value="">
                      {!aircraft.aircraft_type
                        ? "Select aircraft type first"
                        : !visibleTailOptions.length
                          ? "No tail numbers available"
                          : !availableTailCount
                            ? "No available aircraft"
                            : "Select tail number"}
                    </option>
                    {visibleTailOptions.map((option) => (
                      <option
                        key={option.tailNumber}
                        value={option.tailNumber}
                        disabled={!option.available}
                        title={
                          option.asset?.current_location ||
                          "No current location recorded"
                        }
                      >
                        {option.tailNumber} · {currentLocationLabel(option.asset)}
                        {option.available ? "" : ` — ${option.reason}`}
                      </option>
                    ))}
                  </Select>
                  {element.aircraft.length > 1 && (
                    <Select
                      className="air-aircraft-role"
                      aria-label={`Aircraft ${aircraftIndex + 1} element role`}
                      value={aircraft.is_lead ? "element_lead" : "wing_man"}
                      disabled={detailsLocked}
                      onChange={(event) =>
                        setAircraftRole(
                          elementIndex,
                          aircraftIndex,
                          event.target.value
                        )
                      }
                      style={{ flex: "1 1 120px" }}
                    >
                      <option value="element_lead">Element Lead</option>
                      <option value="wing_man">Wing Man</option>
                    </Select>
                  )}
                  <Input
                    className="air-aircraft-pic"
                    aria-label={`Aircraft ${aircraftIndex + 1} pilot in command`}
                    value={crewValue(aircraft, "PIC")}
                    disabled={detailsLocked}
                    onChange={(event) =>
                      setCrewName(
                        elementIndex,
                        aircraftIndex,
                        "PIC",
                        event.target.value
                      )
                    }
                    placeholder="Pilot in Command"
                    style={{ flex: "1 1 130px" }}
                  />
                  <Input
                    className="air-aircraft-cp"
                    aria-label={`Aircraft ${aircraftIndex + 1} co pilot`}
                    value={crewValue(aircraft, "CP")}
                    disabled={detailsLocked}
                    onChange={(event) =>
                      setCrewName(
                        elementIndex,
                        aircraftIndex,
                        "CP",
                        event.target.value
                      )
                    }
                    placeholder="Co Pilot"
                    style={{ flex: "1 1 110px" }}
                  />
                  <Button
                    className="air-aircraft-crew-add"
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={detailsLocked}
                    onClick={() => addCrew(elementIndex, aircraftIndex)}
                  >
                    Add crew
                  </Button>
                  <Button
                    className="air-aircraft-remove"
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => removeAircraft(elementIndex, aircraftIndex)}
                    disabled={detailsLocked || element.aircraft.length === 1}
                    aria-label={`Remove aircraft ${aircraftIndex + 1}`}
                    title="Remove aircraft"
                  >
                    ×
                  </Button>
                </div>

                {selectedTailOption?.asset && (
                  <p
                    className={
                      selectedTailOption.asset.map_location?.mgrs
                        ? "hint air-aircraft-location"
                        : "field-message air-aircraft-warning"
                    }
                  >
                    Current location:{" "}
                    {selectedTailOption.asset.current_location ||
                      "not recorded"}
                    {selectedTailOption.asset.map_location?.mgrs
                      ? ` · MGRS ${selectedTailOption.asset.map_location.mgrs}`
                      : " · no MGRS grid recorded. The Element check will reject this aircraft."}
                    {selectedTailOption.asset.cruising_speed_knots
                      ? ` · Cruising speed ${selectedTailOption.asset.cruising_speed_knots} kn`
                      : " · cruising speed not set; ETA stays manual."}
                  </p>
                )}

                {selectedTailOption && !selectedTailOption.available && (
                  <p className="field-message air-aircraft-warning">
                    Aircraft {selectedTailOption.tailNumber} is unavailable:{" "}
                    {selectedTailOption.reason}. Choose another aircraft.
                  </p>
                )}

                {extraCrew(aircraft).length > 0 && (
                  <div className="air-extra-crew">
                    {extraCrew(aircraft).map((crew) => (
                      <div
                        className="inline-row"
                        key={`crew-${elementIndex}-${aircraftIndex}-${crew.index}`}
                      >
                        <Select
                          aria-label="Additional crew role"
                          value={crew.role || "OTHER"}
                          disabled={detailsLocked}
                          onChange={(event) =>
                            updateCrew(
                              elementIndex,
                              aircraftIndex,
                              crew.index,
                              { role: event.target.value }
                            )
                          }
                        >
                          <option value="IP">IP</option>
                          <option value="OTHER">Other crew</option>
                        </Select>
                        <Input
                          aria-label="Additional crew name"
                          value={crew.name || ""}
                          disabled={detailsLocked}
                          onChange={(event) =>
                            updateCrew(
                              elementIndex,
                              aircraftIndex,
                              crew.index,
                              { name: event.target.value }
                            )
                          }
                          placeholder="Name"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          disabled={detailsLocked}
                          onClick={() =>
                            removeCrew(elementIndex, aircraftIndex, crew.index)
                          }
                          aria-label="Remove additional crew"
                        >
                          ×
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              );
            })}

            <label className="air-inline-label air-callsign-row">
              <span>Callsign</span>
              <Input
                value={callsign}
                disabled={detailsLocked}
                onChange={(event) =>
                  setElementCallsign(elementIndex, event.target.value)
                }
                onBlur={() => {
                  if (currentCount >= 2) {
                    setElementCallsign(
                      elementIndex,
                      formationCallsign(callsign)
                    );
                  }
                }}
                placeholder="Optional"
              />
            </label>

            <hr className="air-divider" />

            {legs.map((leg, legIndex) => (
              <div className="air-leg-block" key={`leg-${elementIndex}-${legIndex}`}>
                <div className="air-itinerary-row">
                  <label className="air-inline-label air-leg-origin">
                    <span>Origin</span>
                    <Input
                      value={
                        legIndex === 0
                          ? leg.origin || ""
                          : legs[legIndex - 1]?.overflight
                            ? `Over (${
                                legs[legIndex - 1]?.destination || "previous point"
                              })`
                            : leg.origin || ""
                      }
                      placeholder={
                        legIndex === 0
                          ? "Selected aircraft location"
                          : "Auto from previous destination"
                      }
                      disabled
                      title={
                        legIndex === 0
                          ? "Departure location from the selected lead aircraft's Air Assets current location"
                          : legs[legIndex - 1]?.overflight
                            ? "Previous itinerary was overflight; origin is over that point"
                            : "Automatically filled from the previous destination"
                      }
                    />
                  </label>
                  <label className="air-inline-label air-leg-destination">
                    <span>Destination</span>
                    <span className="air-location-input">
                      <Input
                        value={leg.destination || ""}
                        disabled={legIsLocked(leg)}
                        onChange={(event) =>
                          patchLeg(elementIndex, legIndex, {
                            destination: event.target.value,
                          })
                        }
                        placeholder="HTOG 11"
                        list={`leg-destination-${elementIndex}-${legIndex}-options`}
                        autoComplete="off"
                      />
                      <button
                        type="button"
                        className="air-map-pin"
                        aria-label={`Open operational map for itinerary ${legIndex + 1} destination`}
                        title="Choose destination from Operational Mapping"
                        disabled={legIsLocked(leg)}
                        onClick={() =>
                          setLegLocationTarget({
                            elementIndex,
                            legIndex,
                            field: "destination",
                            initialQuery: leg.destination || "",
                          })
                        }
                      >
                        <Icon name="map" size={14} />
                      </button>
                      <datalist
                        id={`leg-destination-${elementIndex}-${legIndex}-options`}
                      >
                        {locationSuggestions.map((value) => (
                          <option key={value} value={value} />
                        ))}
                      </datalist>
                    </span>
                  </label>
                  <label className="air-inline-label air-point-role">
                    <span>Point role</span>
                    <Select
                      value={leg.point_role || ""}
                      disabled={legIsLocked(leg)}
                      onChange={(event) =>
                        patchLeg(elementIndex, legIndex, {
                          point_role: event.target.value,
                        })
                      }
                      title="Target or Landing Zone legs show their MGRS in the route summary"
                    >
                      <option value="">Waypoint</option>
                      <option value="target">Target</option>
                      <option value="landing_zone">Landing Zone</option>
                    </Select>
                  </label>
                  <label className="air-inline-label air-leg-eta">
                    <span>ETA</span>
                    <DateTimeInput
                      id={`eta-${elementIndex}-${legIndex}`}
                      value={leg.eta || ""}
                      minDateTime={earliestEta || undefined}
                      disabled={legIsLocked(leg)}
                      title="ETA is calculated from the aircraft cruising speed and MGRS distance. A typed ETA is a manual override."
                      error={Boolean(
                        !legIsLocked(leg) &&
                          earliestEta &&
                          leg.eta &&
                          leg.eta < earliestEta
                      )}
                      onChange={(next) =>
                        patchLeg(elementIndex, legIndex, { eta: next })
                      }
                    />
                  </label>
                  <label className="air-inline-check">
                    <input
                      type="checkbox"
                      checked={Boolean(leg.overflight)}
                      disabled={legIsLocked(leg)}
                      onChange={(event) =>
                        patchLeg(elementIndex, legIndex, {
                          overflight: event.target.checked,
                        })
                      }
                    />
                    No landing (overflight)
                  </label>
                  <Button
                    className="air-leg-remove"
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => removeLeg(elementIndex, legIndex)}
                    disabled={
                      detailsLocked || legs.length === 1 || legIsLocked(leg)
                    }
                    aria-label={`Remove itinerary ${legIndex + 1}`}
                    title="Remove itinerary"
                  >
                    ×
                  </Button>
                </div>

                {!legIsLocked(leg) &&
                  earliestEta &&
                  leg.eta &&
                  leg.eta < earliestEta && (
                  <p className="field-message air-leg-error">
                    ETA must be after the mission ETD of{" "}
                    {formatDateTime(missionEtd)}.
                  </p>
                )}
              </div>
            ))}

            <div>
              <Button
                className="air-add-leg"
                type="button"
                variant="ghost"
                size="sm"
                disabled={detailsLocked}
                onClick={() => addLeg(elementIndex)}
              >
                Add Itinerary
              </Button>
            </div>

            <div className="air-route-row">
              <span>Itinerary / route</span>
              <Input value={itineraryRoute(legs)} disabled readOnly />
            </div>
          </div>
        );
      })}

      <div className="form-section" style={{ marginTop: 16 }}>
        <h3>Preposition / mission sequence</h3>
        <div className="form-grid">
          <Field
            label="Depends on mission"
            className="span-2"
            hint="Only flights that used an aircraft on this sheet and delivered it to that element's first origin are listed."
          >
            <Select
              value={dependsOn || ""}
              disabled={detailsLocked}
              onChange={(event) => onChooseDependency(event.target.value)}
            >
              <option value="">No dependency — this mission starts independently</option>
              {dependencyOptions.map((row) => {
                const arrival = row.air_detail?.destination_location;
                return (
                  <option key={row.id} value={row.id}>
                    {row.mission_number} · {row.title} · {row.status_label || row.status}
                    {arrival ? ` · to ${arrival}` : ""}
                  </option>
                );
              })}
            </Select>
          </Field>
          {!dependencyOptions.length && !dependsOn && (
            <p className="hint span-2">
              No related preposition flight is available yet. Select the aircraft
              and enter the element origin to narrow this list.
            </p>
          )}
          {selectedDependency && (
            <p
              className={
                dependencyArrival ? "hint span-2" : "field-message span-2"
              }
            >
              {dependencyArrival
                ? `Incoming aircraft ETA: ${formatDateTime(dependencyArrival)}. This mission's ETD is set to that time.`
                : "The selected predecessor has no ETA. Open it and set the ETA on its itinerary before linking it."}
            </p>
          )}
          {dependsOn && (
            <>
              <Field
                label="Release when"
                hint="Completion keeps this mission's ATD on hold until the preposition mission is completed."
              >
                <Select
                  value={dependencyRelease || "completion"}
                  disabled={detailsLocked}
                  onChange={(event) =>
                    onDependencyChange("dependency_release", event.target.value)
                  }
                >
                  <option value="completion">Predecessor mission is completed</option>
                  <option value="rendezvous_ready">Predecessor is rendezvous-ready</option>
                </Select>
              </Field>
              <Field
                label="Arrival buffer (minutes)"
                hint="A separated Element must use 0 so its ETD matches the incoming aircraft's arrival exactly."
              >
                <Input
                  type="number"
                  min="0"
                  max="240"
                  step="1"
                  disabled={detailsLocked}
                  value={dependencyBuffer ?? 0}
                  onChange={(event) =>
                    onDependencyChange("dependency_buffer_minutes", event.target.value)
                  }
                />
              </Field>
            </>
          )}
          {dependencyError && (
            <p className="field-message span-2">
              Could not load missions: {dependencyError}
            </p>
          )}
        </div>
      </div>
    </div>

    {legLocationTarget && (
      <OperationalLocationPickerModal
        title={`Select itinerary ${legLocationTarget.legIndex + 1} ${legLocationTarget.field}`}
        initialQuery={legLocationTarget.initialQuery || ""}
        allowManual
        includeUnits
        includeAirfields
        excludeFeatureTypes={AIR_LEG_EXCLUDED_FEATURE_TYPES}
        searchExternal
        onClose={() => setLegLocationTarget(null)}
        onApply={applyLegLocation}
      />
    )}
    </>
  );
}
