import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, qs } from "../api/client.js";
import { useAuth } from "../auth/AuthContext.jsx";
import LocationPickerModal from "../components/LocationPickerModal.jsx";
import OperationalLocationPickerModal from "../components/OperationalLocationPickerModal.jsx";
import AirFlightOperationsFields from "../components/AirFlightOperationsFields.jsx";
import SearchableSelect from "../components/SearchableSelect.jsx";
import {
  Button,
  Card,
  DateTimeInput,
  EmptyState,
  ErrorBanner,
  Field,
  Input,
  Modal,
  Select,
  Tabs,
  Textarea,
} from "../components/ui.jsx";
import { useApi } from "../hooks/useApi.js";
import {
  currentTimeManila,
  formatDateTime,
  normalizeTimeInput,
  toApiDateTime,
  toInputDateTime,
  todayManila,
} from "../utils/format.js";
import { byRank } from "../utils/ranks.js";
import { refreshNotifications } from "../utils/notifications.js";

//: Posts that may hold approval authority. The API sends its own copy; this is
//: used when that response has not arrived (or was refused), so the post list is
//: never an empty dropdown.
const APPROVING_POSTS = [
  { value: "commander", label: "Commander" },
  { value: "deputy_commander", label: "Deputy Commander" },
  { value: "director_operations", label: "Director for Operations" },
  { value: "operations_officer", label: "Operations Officer" },
];

//: The command ladder a configured approval route follows, most junior first.
//: The chosen final authority decides where the route stops.
const ROUTE_ROLE_ORDER = [
  "operations_officer",
  "director_operations",
  "deputy_commander",
  "commander",
];
const ROUTE_ROLE_LABELS = {
  operations_officer: "Operations Officer",
  director_operations: "Director for Operations",
  deputy_commander: "Deputy Commander",
  commander: "Commander",
};

function todayInput() {
  return todayManila();
}

const EMPTY = {
  category: "ground",
  title: "",
  purpose: "",
  unit: "",
  office_section: "",
  location_name: "",
  latitude: "",
  longitude: "",
  mgrs: "",
  remarks: "",
  depends_on: "",
  dependency_release: "completion",
  dependency_buffer_minutes: 0,
  ground: {
    estimated_start: "",
    estimated_end: "",
    operational_area: "",
    ground_risk_notes: "",
    // Extra areas beyond the one picked with the mission location. Empty by
    // default: the form never asks for a second area unless the planner adds it.
    additional_areas: [],
  },
  air: {
    operation_date: todayInput(),
    operation_time: "",
    elements: [
      {
        mission: "",
        aircraft: [
          {
            aircraft_type: "",
            tail_number: "",
            callsign: "",
            is_lead: true,
            crew: [
              { role: "PIC", name: "" },
              { role: "CP", name: "" },
            ],
            itineraries: [
              {
                etd: "",
                eta: "",
                origin: "",
                destination: "",
                route: "",
                mission: "",
                point_role: "",
                overflight: false,
                return_to_origin: false,
              },
            ],
          },
        ],
      },
    ],
    supporting_units: [],
    mission_configuration: "single",
    aircraft_count: 1,
    air_asset_ids: [],
    air_asset: "",
    aircraft_type: "",
    aircraft_tail_number: "",
    flight_identification: "",
    air_pilots: [],
    air_copilots: [],
    departure_location: "",
    destination_location: "",
    route: "",
    scheduled_departure: "",
    scheduled_arrival: "",
  },
};

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
    itineraries: [
      {
        etd: "",
        eta: "",
        origin: "",
        destination: "",
        route: "",
        mission: "",
        point_role: "",
        overflight: false,
        return_to_origin: false,
      },
    ],
  };
}

function normaliseAirElements(air, assignedAircraft) {
  const rows = (air.elements || []).map((element, elementIndex) => ({
    mission: element.mission || "",
    aircraft: (element.aircraft || []).map((aircraft, aircraftIndex) => ({
      aircraft_type: aircraft.aircraft_type || "",
      tail_number: aircraft.tail_number || "",
      callsign: aircraft.callsign || "",
      is_lead: Boolean(aircraft.is_lead ?? aircraftIndex === 0),
      crew: (aircraft.crew || []).map((row) => ({
        role: row.role || "PIC",
        name: row.name || "",
      })),
      itineraries: (aircraft.itineraries || []).map((leg) => ({
        id: leg.id || null,
        etd: toInputDateTime(leg.etd),
        eta: toInputDateTime(leg.eta),
        atd: toInputDateTime(leg.atd),
        ata: toInputDateTime(leg.ata),
        tot: toInputDateTime(leg.tot),
        toot: toInputDateTime(leg.toot),
        status: leg.status || "planned",
        origin: leg.origin || "",
        destination: leg.destination || "",
        route: leg.route || "",
        mission: leg.mission || "",
        point_role: leg.point_role || "",
        overflight: Boolean(leg.overflight),
        return_to_origin: Boolean(leg.return_to_origin),
      })),
    })),
  }));
  if (rows.length) return rows;

  const aircraftRows = (assignedAircraft || []).length
    ? assignedAircraft
    : [null];
  return [
    {
      mission: "",
      aircraft: aircraftRows.map((row, index) => ({
        aircraft_type: row?.aircraft_model || air.aircraft_type || "",
        tail_number: row?.tail_number || air.aircraft_tail_number || "",
        callsign: row?.callsign || air.flight_identification || "",
        is_lead: index === 0,
        crew: [
          row?.pilot_in_command
            ? { role: "PIC", name: row.pilot_in_command }
            : { role: "PIC", name: "" },
          ...(row?.co_pilot ? [{ role: "CP", name: row.co_pilot }] : []),
        ],
        itineraries: [
          {
            etd: toInputDateTime(air.scheduled_departure),
            eta: toInputDateTime(air.scheduled_arrival),
            origin: air.departure_location || "",
            destination: air.destination_location || "",
            route: air.route || "",
            mission: "",
            point_role: "",
            overflight: false,
            return_to_origin: false,
          },
        ],
      })),
    },
  ];
}

//: Mission configuration controls how many registered aircraft the form asks
//: for. The ranges mirror the server-side bounds on AirMissionDetail.
const AIR_CONFIGURATIONS = [
  { value: "single", label: "1 · Single Aircraft", min: 1, max: 1, count: 1 },
  { value: "element", label: "2 · Element", min: 2, max: 2, count: 2 },
  { value: "three_ship", label: "3 · Three-Ship", min: 3, max: 3, count: 3 },
  { value: "flight", label: "4 · Flight", min: 4, max: 4, count: 4 },
  {
    value: "multi_aircraft",
    label: "5–6 · Multi-Aircraft",
    min: 5,
    max: 6,
    count: 5,
  },
  {
    value: "large_formation",
    label: "7–12 · Large Formation",
    min: 7,
    max: 12,
    count: 7,
  },
  {
    value: "custom_formation",
    label: "13+ · Custom Formation",
    min: 13,
    max: 50,
    count: 13,
  },
];

function airConfiguration(value) {
  return AIR_CONFIGURATIONS.find((row) => row.value === value) || AIR_CONFIGURATIONS[0];
}

const EMPTY_STOP = { location_name: "", mgrs: "", latitude: "", longitude: "", remarks: "" };

/** A picked operational-mapping entry as one readable field value. */
function operationalLocationLine(picked) {
  const detail = picked.detail || {};
  const parts = [
    detail.label || picked.location_name,
    detail.address,
    detail.mgrs || picked.mgrs,
  ]
    .map((part) => (part || "").trim())
    .filter(Boolean);
  return parts
    .filter(
      (part, index) =>
        parts.findIndex((item) => item.toLowerCase() === part.toLowerCase()) === index
    )
    .join(", ");
}

// A POV is somebody's own vehicle: the Operations Center describes it on the
// trip itself, and its plan key is kept apart from register vehicle ids.
const POV_PREFIX = "pov:";
const POV_FUELS = ["Gasoline", "Diesel", "LPG", "Electric"];
const POV_TYPES = ["Motorcycle", "Car", "SUV", "Pickup", "Van", "Truck", "Bus", "Other"];

//: Designations a planner can give to a person on the mission. The same list
//: is used for every row so the roster reads consistently.
const PERSONNEL_ROLES = [
  "Officer-In-Charge",
  "Team Leader",
  "Assistant Team Leader",
  "Driver",
  "Security",
  "Member",
  "Specify",
];

/** Compact mobile controls for an itinerary row. */
function StopActionIcon({ name }) {
  const paths = {
    map: (
      <>
        <path d="M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11Z" />
        <circle cx="12" cy="10" r="2.5" />
      </>
    ),
    operational: (
      <>
        <path d="m12 3 9 5-9 5-9-5 9-5Z" />
        <path d="m3 13 9 5 9-5" />
      </>
    ),
    remove: <path d="M6 6l12 12M18 6 6 18" />,
  };
  return (
    <span className="stop-action-icon" aria-hidden="true">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {paths[name]}
      </svg>
    </span>
  );
}

export default function MissionFormPage() {
  const navigate = useNavigate();
  const { user, permissions } = useAuth();
  const canPlan = Boolean(permissions?.can_plan_missions);
  const [searchParams] = useSearchParams();
  // Opening the form at /missions/:id/edit revises a mission that already
  // exists (the Operations Center's side of "returned for revision"); the same
  // fields are prefilled from the record and saved with PATCH.
  const { id: editId } = useParams();
  const editing = Boolean(editId);
  const existing = useApi(editId ? `/missions/${editId}/` : "", {
    enabled: canPlan && editing,
    deps: [editId],
  });
  // An MRM that is already approved is evidence, not a draft: it is shown
  // read-only while revising unless the planner explicitly unlinks it.
  const approvedMrm = Boolean(editing && existing.data?.mrm?.status === "approved");
  // The decision authority is named when the mission is first routed; moving
  // it silently would reassign an approval somebody already accepted.
  const authorityLocked = Boolean(editing && existing.data?.approval?.authority_id);
  // Once any leg has actual times, the aircraft, crew, schedule and finished
  // legs are evidence of what was flown. Only the unflown itinerary may change.
  const airMissionLocked = useMemo(() => {
    const mission = existing.data;
    if (!editing || mission?.category !== "air") return false;
    if (mission.status === "active") return true;
    return (mission.air_detail?.elements || []).some((element) =>
      (element.aircraft || []).some((aircraft) =>
        (aircraft.itineraries || []).some(
          (leg) =>
            leg.atd ||
            leg.ata ||
            leg.tot ||
            leg.toot ||
            leg.status === "cancelled"
        )
      )
    );
  }, [editing, existing.data]);
  const [forceMrmUnlink, setForceMrmUnlink] = useState(false);
  // Which submit button started the save: plain update, or update + resubmit.
  const submitIntent = useRef("save");
  // Opened from Ground or Air Missions with ?category=... so the form starts in
  // the register the planner came from; the Category tabs still switch it.
  const requestedCategory = searchParams.get("category") === "air" ? "air" : "ground";

  const [form, setForm] = useState(() => ({ ...EMPTY, category: requestedCategory }));
  const [personnel, setPersonnel] = useState([]);
  const [mrmCreator, setMrmCreator] = useState("");
  const [mrmEvaluator, setMrmEvaluator] = useState("");
  const [errors, setErrors] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [createdMission, setCreatedMission] = useState(null);
  const [prefilled, setPrefilled] = useState(false);
  // The personnel picker is a modal of names: people are chosen there, then
  // each added row gets its own designation.
  const [personPickerOpen, setPersonPickerOpen] = useState(false);
  const [personQuery, setPersonQuery] = useState("");
  const [personPicks, setPersonPicks] = useState([]);
  // Which target the map picker is filling: the mission location (which also
  // sets the operational area, so it is only ever picked once) or one of the
  // additional operational areas.
  const [locationTarget, setLocationTarget] = useState(null);
  // The dedicated Operational Mapping picker, for the mission Location or a
  // single itinerary stop.
  const [operationalPickerOpen, setOperationalPickerOpen] = useState(false);
  const [operationalTarget, setOperationalTarget] = useState(null);
  const [destinationPickerOpen, setDestinationPickerOpen] = useState(false);
  // The destination AOR's unit when the pin leaves this unit's own area. Set
  // from the server so the planner is asked before the mission is saved.
  const [aorHandoff, setAorHandoff] = useState(null);
  const [notifyDestination, setNotifyDestination] = useState(true);

  const [tripOpen, setTripOpen] = useState(false);
  const [trip, setTrip] = useState({
    // Blank until the planner overrides it: the field then shows the mission's
    // own title, so the trip is named after the mission by default.
    title: "",
    departure_at: "",
    return_at: "",
    remarks: "",
  });
  // One itinerary per vehicle: two vehicles are never assumed to follow the
  // same route, because they usually do not.
  const [tripPlans, setTripPlans] = useState({});
  // One designated driver per selected vehicle, keyed by vehicle id. A vehicle
  // cannot join the trip until its own driver is named.
  const [tripDrivers, setTripDrivers] = useState({});
  // Privately owned vehicles described on this trip, one per person's vehicle.
  const [povs, setPovs] = useState([]);
  const [povSeq, setPovSeq] = useState(0);
  const [activeVehicle, setActiveVehicle] = useState(null);
  const [draggedStop, setDraggedStop] = useState(null);
  const [dragOverStop, setDragOverStop] = useState(null);
  const [draggedAirSlot, setDraggedAirSlot] = useState(null);
  const [dragOverAirSlot, setDragOverAirSlot] = useState(null);

  // The approving authority is chosen by the Operations Center and is a
  // person, not only a post: the task has to land in somebody's queue.
  const [authorityRole, setAuthorityRole] = useState("");
  const [authorityUser, setAuthorityUser] = useState("");
  const [authorityNote, setAuthorityNote] = useState("");
  // The Mission Order is drafted with the mission and signed after approval.
  // Naming the signatory here puts that post-approval task in the plan.
  const [orderSignatory, setOrderSignatory] = useState("");

  // Fill the form once, when the stored mission arrives. Later reloads must not
  // overwrite what the planner is typing.
  useEffect(() => {
    if (!editing || prefilled || !existing.data) return;
    const mission = existing.data;
    const ground = mission.ground_detail || {};
    const air = mission.air_detail || {};
    const assignedAircraft = air.assigned_aircraft || [];
    setForm({
      ...EMPTY,
      category: mission.category || "ground",
      title: mission.title || "",
      purpose: mission.purpose || "",
      unit: mission.unit ? String(mission.unit) : "",
      office_section: mission.office_section || "",
      location_name: mission.location_name || "",
      latitude: mission.latitude ?? "",
      longitude: mission.longitude ?? "",
      mgrs: mission.mgrs || "",
      remarks: mission.remarks || "",
      depends_on: mission.depends_on ? String(mission.depends_on) : "",
      dependency_release: mission.dependency_release || "completion",
      dependency_buffer_minutes: mission.dependency_buffer_minutes ?? 0,
      ground: {
        estimated_start: toInputDateTime(ground.estimated_start || mission.start_at),
        estimated_end: toInputDateTime(ground.estimated_end || mission.end_at),
        operational_area: ground.operational_area || "",
        ground_risk_notes: ground.ground_risk_notes || "",
        additional_areas: (mission.operational_areas || []).map((row) => ({
          name: row.name || "",
          latitude: row.latitude ?? "",
          longitude: row.longitude ?? "",
          mgrs: row.mgrs || "",
        })),
      },
      air: {
        ...EMPTY.air,
        operation_date:
          air.operation_date ||
          (air.scheduled_departure ? air.scheduled_departure.slice(0, 10) : todayInput()),
        operation_time:
          (air.elements?.[0]?.aircraft?.[0]?.itineraries?.[0]?.etd ||
          air.scheduled_departure)
            ? toInputDateTime(
                air.elements?.[0]?.aircraft?.[0]?.itineraries?.[0]?.etd ||
                  air.scheduled_departure
              )
                .slice(11, 16)
            : "",
        elements: normaliseAirElements(air, assignedAircraft),
        mission_configuration:
          air.mission_configuration || EMPTY.air.mission_configuration,
        aircraft_count: air.aircraft_count || 1,
        air_asset_ids: assignedAircraft
          .map((row) => String(row.air_asset ?? row.air_asset_id ?? row.asset_id ?? ""))
          .filter(Boolean),
        air_pilots: assignedAircraft.map((row) => row.pilot_in_command || ""),
        air_copilots: assignedAircraft.map((row) => row.co_pilot || ""),
        air_asset: air.air_asset ? String(air.air_asset) : "",
        supporting_units: (air.supporting_units || []).map(String),
        aircraft_type: air.aircraft_type || "",
        aircraft_tail_number: air.aircraft_tail_number || "",
        flight_identification: air.flight_identification || "",
        departure_location: air.departure_location || "",
        destination_location: air.destination_location || "",
        route: air.route || "",
        scheduled_departure: toInputDateTime(air.scheduled_departure),
        scheduled_arrival: toInputDateTime(air.scheduled_arrival),
      },
    });
    setPersonnel(
      (mission.personnel || [])
        .filter((row) => row.is_active !== false)
        .map((row) => ({
          user_id: String(row.user),
          role: row.role || "Member",
          custom_role: row.custom_role || "",
        }))
    );
    const team = mission.mrm_team || [];
    const lead = team.find((row) => row.role === "lead");
    const reviewer = team.find((row) => row.role === "reviewer");
    setMrmCreator(lead ? String(lead.user) : "");
    setMrmEvaluator(reviewer ? String(reviewer.user) : "");
    // The authority is locked while revising, but it must show what was set,
    // not an empty "Select a post".
    const approval = mission.approval || {};
    setAuthorityRole(approval.authority_role || "");
    setAuthorityUser(approval.authority_id ? String(approval.authority_id) : "");
    const order = mission.mission_order || {};
    setOrderSignatory(order.signatory_id ? String(order.signatory_id) : "");
    setPrefilled(true);
  }, [editing, prefilled, existing.data]);

  // The UPR selector offers the user's own unit and its authorised
  // subordinates only; the server scopes the same list.
  const units = useApi("/units/?is_active=true&page_size=200&scope=mine", { enabled: canPlan });
  const people = useApi("/personnel/assignable/", { enabled: canPlan });
  const vehicles = useApi("/vehicles/?is_active=true&page_size=200", { enabled: canPlan });
  // Air missions name the friendly unit they support; the list is the friendly
  // troops plotted in Operational Mapping, server-scoped to the caller.
  const supportUnits = useApi(
    "/operational-mapping/?feature_type=friendly_troop&page_size=500",
    { enabled: false }
  );
  // The Daily Flight Operations board picks aircraft types from the register.
  // The server scopes the register to the caller's current unit and subordinates.
  const airAssets = useApi("/inventory/air-assets/?page_size=200");
  // Earlier missions that can release this one. The server enforces the same
  // category and unit rules; the form only offers the visible candidates.
  const dependencyMissions = useApi(
    form.category === "air"
      ? "/missions/?category=air&page_size=200&ordering=start_at"
      : "",
    { enabled: canPlan && form.category === "air", deps: [form.category] }
  );
  // Where each unit sits, used to default stop 1 to the vehicle's unit location.
  // One call covers every pin in the caller's scope; the endpoint is not paginated.
  const unitPins = useApi("/units/markers/", { enabled: canPlan });
  const unitPinById = useMemo(() => {
    const map = new Map();
    for (const row of unitPins.data?.results ?? []) {
      if (row.latitude != null && row.longitude != null) map.set(Number(row.unit_id), row);
    }
    return map;
  }, [unitPins.data]);
  const unitOptions = units.data?.results ?? [];
  const supportUnitOptions = supportUnits.data?.results ?? [];
  const airAssetOptions = airAssets.data?.results ?? [];

  function normalisePlace(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, " ");
  }

  function samePlace(left, right) {
    const a = normalisePlace(left);
    const b = normalisePlace(right);
    if (!a || !b) return false;
    return a === b || a.includes(b) || b.includes(a);
  }

  // A predecessor is relevant only when it flew an aircraft that is on this
  // sheet and delivered that aircraft to the element origin being drafted.
  const draftOriginsByTail = new Map();
  for (const element of form.air.elements || []) {
    const origin = (element.aircraft?.[0]?.itineraries?.[0]?.origin || "").trim();
    if (!origin) continue;
    for (const aircraft of element.aircraft || []) {
      const tail = (aircraft.tail_number || "").trim().toLowerCase();
      if (!tail) continue;
      if (!draftOriginsByTail.has(tail)) draftOriginsByTail.set(tail, []);
      draftOriginsByTail.get(tail).push(origin);
    }
  }

  function dependencyArrivalForTail(mission, tail) {
    const detail = mission.air_detail;
    if (!detail) return "";
    const manual = (detail.elements || [])
      .flatMap((element) => element.aircraft || [])
      .find(
        (aircraft) =>
          (aircraft.tail_number || "").trim().toLowerCase() === tail
      );
    if (manual) {
      const legs = manual.itineraries || [];
      return (
        legs[legs.length - 1]?.destination ||
        detail.destination_location ||
        ""
      );
    }
    const linked = (detail.assigned_aircraft || []).find(
      (aircraft) =>
        (aircraft.tail_number || "").trim().toLowerCase() === tail
    );
    return linked ? detail.destination_location || "" : "";
  }

  const dependencyUnit = form.unit || user?.unit;
  const dependencyOptions = (dependencyMissions.data?.results ?? []).filter(
    (row) => {
      if (String(row.id) === String(editId || "") || row.status === "cancelled") {
        return false;
      }
      if (dependencyUnit && String(row.unit) !== String(dependencyUnit)) {
        return false;
      }
      for (const [tail, origins] of draftOriginsByTail.entries()) {
        const arrival = dependencyArrivalForTail(row, tail);
        if (origins.some((origin) => samePlace(arrival, origin))) return true;
      }
      return false;
    }
  );
  const dependencyOptionIds = dependencyOptions
    .map((row) => String(row.id))
    .join(",");

  // If the draft changes so the chosen predecessor is no longer related, clear
  // it rather than silently saving a mission that this draft does not release.
  useEffect(() => {
    if (!form.depends_on) return;
    if (editing && !prefilled) return;
    if (
      dependencyMissions.loading ||
      dependencyMissions.error ||
      airAssets.loading ||
      airAssets.error
    ) {
      return;
    }
    const available = dependencyOptionIds
      ? new Set(dependencyOptionIds.split(","))
      : new Set();
    if (!available.has(String(form.depends_on))) {
      setForm((current) => ({ ...current, depends_on: "" }));
    }
  }, [
    dependencyOptionIds,
    dependencyMissions.loading,
    dependencyMissions.error,
    airAssets.loading,
    airAssets.error,
    form.depends_on,
    editing,
    prefilled,
  ]);

  const selectedDependency = dependencyOptions.find(
    (row) => String(row.id) === String(form.depends_on)
  );
  const dependencyBase =
    selectedDependency?.air_detail?.rendezvous_ready_at ||
    selectedDependency?.air_detail?.actual_arrival ||
    selectedDependency?.air_detail?.scheduled_arrival ||
    null;
  const dependencyEarliest = dependencyBase
    ? new Date(
        new Date(dependencyBase).getTime() +
          (Number(form.dependency_buffer_minutes) || 0) * 60000
      ).toISOString()
    : null;
  const dependencyEarliestInput = dependencyEarliest
    ? toInputDateTime(dependencyEarliest)
    : "";
  const departureBeforeDependency = Boolean(
    dependencyEarliestInput &&
      form.air.scheduled_departure &&
      form.air.scheduled_departure < dependencyEarliestInput
  );
  const selectedSupportUnits = form.air.supporting_units
    .map((id) => supportUnitOptions.find((row) => String(row.id) === String(id)))
    .filter(Boolean);
  const selectedAirAsset = airAssetOptions.find(
    (row) => String(row.id) === String(form.air.air_asset)
  );
  const airConfig = airConfiguration(form.air.mission_configuration);
  const airCount = Math.max(1, Number(form.air.aircraft_count) || 1);
  const airSlots = Array.from(
    { length: airCount },
    (_, index) => form.air.air_asset_ids[index] || ""
  );
  const chosenAirAssets = airSlots
    .map((id) => airAssetOptions.find((row) => String(row.id) === String(id)))
    .filter(Boolean);
  const unavailableChosenAirAssets = chosenAirAssets.filter(
    (asset) => asset.operational_status !== "available"
  );
  // The Flight Operations sheet stores aircraft as manual element rows, so the
  // availability check covers every airframe in the register, not only the
  // formation slots used by the older linked-aircraft form.
  const airOperationTime = normalizeTimeInput(form.air.operation_time);
  const airOperationEtd =
    form.air.operation_date && airOperationTime
      ? `${form.air.operation_date}T${airOperationTime}`
      : "";
  const airLegEtas = (form.air.elements || [])
    .flatMap((element) => element.aircraft || [])
    .flatMap((aircraft) => aircraft.itineraries || [])
    .map((leg) => leg.eta)
    .filter(Boolean);
  const airLastEta = airLegEtas.reduce(
    (latest, value) => (value > latest ? value : latest),
    ""
  );
  const airConflictAssetIds = Array.from(
    new Set(airAssetOptions.map((row) => String(row.id)).filter(Boolean))
  );
  const airConflictStart = toApiDateTime(airOperationEtd);
  const airConflictEnd = toApiDateTime(airLastEta);
  const airConflictWindowReady = Boolean(
    airConflictAssetIds.length &&
      airConflictStart &&
      airConflictEnd &&
      airConflictEnd > airConflictStart
  );
  const airConflicts = useApi(
    airConflictWindowReady
      ? `/inventory/air-assets/conflicts/${qs({
          air_asset_ids: airConflictAssetIds.join(","),
          start: airConflictStart,
          end: airConflictEnd,
          exclude_mission: editId || "",
        })}`
      : "",
    {
      enabled: canPlan && form.category === "air" && airConflictWindowReady,
      deps: [airConflictWindowReady, airConflictAssetIds.join(","), editId],
    }
  );
  const airConflictRows = airConflicts.data?.conflicts ?? [];
  const selectedUnit = unitOptions.find((row) => String(row.id) === String(form.unit));
  const unitLabel = selectedUnit ? selectedUnit.acronym : "your unit";
  const isGround = form.category === "ground";

  // A flight can support several plotted units; the picker appends one at a
  // time so the list stays readable and each choice can be taken back.
  function addSupportUnit(value) {
    if (!value) return;
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        supporting_units: current.air.supporting_units.includes(String(value))
          ? current.air.supporting_units
          : [...current.air.supporting_units, String(value)],
      },
    }));
  }

  function removeSupportUnit(value) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        supporting_units: current.air.supporting_units.filter(
          (id) => String(id) !== String(value)
        ),
      },
    }));
  }

  // A configuration change resizes the ordered aircraft slots. Existing
  // choices keep their positions; a lead that falls outside the new size is
  // cleared rather than silently pointing at an unassigned aircraft.
  function chooseDependency(value) {
    setForm((current) => {
      const next = { ...current, depends_on: value };
      if (!value) return next;
      const dependency = dependencyOptions.find(
        (row) => String(row.id) === String(value)
      );
      const base =
        dependency?.air_detail?.rendezvous_ready_at ||
        dependency?.air_detail?.actual_arrival ||
        dependency?.air_detail?.scheduled_arrival ||
        null;
      if (!base) return next;
      const buffer = Number(current.dependency_buffer_minutes) || 0;
      const earliest = toInputDateTime(
        new Date(new Date(base).getTime() + buffer * 60000).toISOString()
      );
      // The air form's ETD is operation_date + operation_time, not the
      // scheduled_departure field alone. Set both so choosing the preposition
      // actually moves the joint mission to the incoming aircraft's arrival.
      next.air = {
        ...current.air,
        scheduled_departure: earliest,
        operation_date: earliest.slice(0, 10),
        operation_time: earliest.slice(11, 16),
      };
      return next;
    });
  }

  function chooseAirConfiguration(value) {
    const configuration = airConfiguration(value);
    setForm((current) => {
      const ids = Array.from(
        { length: configuration.count },
        (_, index) => current.air.air_asset_ids[index] || ""
      );
      const pilots = Array.from(
        { length: configuration.count },
        (_, index) => current.air.air_pilots?.[index] || ""
      );
      const copilots = Array.from(
        { length: configuration.count },
        (_, index) => current.air.air_copilots?.[index] || ""
      );
      const previousLead = airAssetOptions.find(
        (row) => String(row.id) === String(current.air.air_asset)
      );
      const lead = ids.includes(current.air.air_asset)
        ? current.air.air_asset
        : ids.find(Boolean) || "";
      const leadAsset = airAssetOptions.find((row) => String(row.id) === String(lead));
      const callsignWasAutomatic =
        !current.air.flight_identification ||
        (previousLead &&
          current.air.flight_identification === (previousLead.callsign || ""));
      const departureWasAutomatic =
        !current.air.departure_location ||
        (previousLead &&
          current.air.departure_location === (previousLead.current_location || ""));
      return {
        ...current,
        air: {
          ...current.air,
          mission_configuration: value,
          aircraft_count: configuration.count,
          air_asset_ids: ids,
          air_pilots: pilots,
          air_copilots: copilots,
          air_asset: lead,
          departure_location:
            leadAsset && departureWasAutomatic
              ? leadAsset.current_location || ""
              : current.air.departure_location,
          aircraft_type: leadAsset ? leadAsset.model || "" : current.air.aircraft_type,
          aircraft_tail_number: leadAsset
            ? leadAsset.tail_number || ""
            : current.air.aircraft_tail_number,
          flight_identification:
            leadAsset && callsignWasAutomatic
              ? leadAsset.callsign || ""
              : current.air.flight_identification,
        },
      };
    });
  }

  function chooseAirCount(value) {
    const count = Math.min(airConfig.max, Math.max(airConfig.min, Number(value)));
    setForm((current) => {
      const ids = Array.from(
        { length: count },
        (_, index) => current.air.air_asset_ids[index] || ""
      );
      const pilots = Array.from(
        { length: count },
        (_, index) => current.air.air_pilots?.[index] || ""
      );
      const copilots = Array.from(
        { length: count },
        (_, index) => current.air.air_copilots?.[index] || ""
      );
      const lead = ids.includes(current.air.air_asset)
        ? current.air.air_asset
        : ids.find(Boolean) || "";
      const previousLead = airAssetOptions.find(
        (row) => String(row.id) === String(current.air.air_asset)
      );
      const leadAsset = airAssetOptions.find((row) => String(row.id) === String(lead));
      const callsignWasAutomatic =
        !current.air.flight_identification ||
        (previousLead &&
          current.air.flight_identification === (previousLead.callsign || ""));
      const departureWasAutomatic =
        !current.air.departure_location ||
        (previousLead &&
          current.air.departure_location === (previousLead.current_location || ""));
      return {
        ...current,
        air: {
          ...current.air,
          aircraft_count: count,
          air_asset_ids: ids,
          air_pilots: pilots,
          air_copilots: copilots,
          air_asset: lead,
          departure_location:
            leadAsset && departureWasAutomatic
              ? leadAsset.current_location || ""
              : current.air.departure_location,
          aircraft_type: leadAsset ? leadAsset.model || "" : current.air.aircraft_type,
          aircraft_tail_number: leadAsset
            ? leadAsset.tail_number || ""
            : current.air.aircraft_tail_number,
          flight_identification:
            leadAsset && callsignWasAutomatic
              ? leadAsset.callsign || ""
              : current.air.flight_identification,
        },
      };
    });
  }

  function chooseFormationAsset(index, value) {
    setForm((current) => {
      const count = Math.max(1, Number(current.air.aircraft_count) || 1);
      const ids = Array.from(
        { length: count },
        (_, position) => current.air.air_asset_ids[position] || ""
      );
      ids[index] = value;
      const previousLead = airAssetOptions.find(
        (row) => String(row.id) === String(current.air.air_asset)
      );
      let lead = current.air.air_asset;
      if (lead && !ids.includes(lead)) lead = "";
      if (!lead) lead = ids.find(Boolean) || "";
      const leadAsset = airAssetOptions.find((row) => String(row.id) === String(lead));
      const callsignWasAutomatic =
        !current.air.flight_identification ||
        (previousLead &&
          current.air.flight_identification === (previousLead.callsign || ""));
      const departureWasAutomatic =
        !current.air.departure_location ||
        (previousLead &&
          current.air.departure_location === (previousLead.current_location || ""));
      return {
        ...current,
        air: {
          ...current.air,
          air_asset_ids: ids,
          air_asset: lead,
          departure_location:
            leadAsset && departureWasAutomatic
              ? leadAsset.current_location || ""
              : current.air.departure_location,
          aircraft_type: leadAsset ? leadAsset.model || "" : current.air.aircraft_type,
          aircraft_tail_number: leadAsset
            ? leadAsset.tail_number || ""
            : current.air.aircraft_tail_number,
          flight_identification:
            leadAsset && callsignWasAutomatic
              ? leadAsset.callsign || ""
              : current.air.flight_identification,
        },
      };
    });
  }

  function chooseElementRole(index, role) {
    setForm((current) => {
      const count = Math.max(1, Number(current.air.aircraft_count) || 1);
      const ids = Array.from(
        { length: count },
        (_, position) => current.air.air_asset_ids[position] || ""
      );
      const selectedId = ids[index];
      if (!selectedId) return current;
      let nextLead = current.air.air_asset;
      if (role === "element_lead") {
        nextLead = selectedId;
      } else {
        if (String(nextLead) !== String(selectedId)) return current;
        const replacement = ids.find((id, position) => id && position !== index);
        if (!replacement) return current;
        nextLead = replacement;
      }
      const previousLead = airAssetOptions.find(
        (row) => String(row.id) === String(current.air.air_asset)
      );
      const asset = airAssetOptions.find((row) => String(row.id) === String(nextLead));
      const callsignWasAutomatic =
        !current.air.flight_identification ||
        (previousLead &&
          current.air.flight_identification === (previousLead.callsign || ""));
      const departureWasAutomatic =
        !current.air.departure_location ||
        (previousLead &&
          current.air.departure_location === (previousLead.current_location || ""));
      return {
        ...current,
        air: {
          ...current.air,
          air_asset: nextLead,
          departure_location:
            asset && departureWasAutomatic
              ? asset.current_location || ""
              : current.air.departure_location,
          aircraft_type: asset ? asset.model || "" : current.air.aircraft_type,
          aircraft_tail_number: asset
            ? asset.tail_number || ""
            : current.air.aircraft_tail_number,
          flight_identification:
            asset && callsignWasAutomatic
              ? asset.callsign || ""
              : current.air.flight_identification,
        },
      };
    });
  }

  function setAirCrew(index, field, value) {
    setForm((current) => {
      const count = Math.max(1, Number(current.air.aircraft_count) || 1);
      const rows = Array.from(
        { length: count },
        (_, position) => current.air[field]?.[position] || ""
      );
      rows[index] = value;
      return { ...current, air: { ...current.air, [field]: rows } };
    });
  }

  function clearAirSlot(index) {
    setForm((current) => {
      const count = Math.max(1, Number(current.air.aircraft_count) || 1);
      const ids = Array.from(
        { length: count },
        (_, position) => current.air.air_asset_ids[position] || ""
      );
      const pilots = Array.from(
        { length: count },
        (_, position) => current.air.air_pilots?.[position] || ""
      );
      const copilots = Array.from(
        { length: count },
        (_, position) => current.air.air_copilots?.[position] || ""
      );
      const previousLead = airAssetOptions.find(
        (row) => String(row.id) === String(current.air.air_asset)
      );
      const removedId = ids[index];
      ids[index] = "";
      pilots[index] = "";
      copilots[index] = "";
      const lead =
        String(current.air.air_asset) === String(removedId)
          ? ids.find(Boolean) || ""
          : current.air.air_asset;
      const asset = airAssetOptions.find((row) => String(row.id) === String(lead));
      const callsignWasAutomatic =
        !current.air.flight_identification ||
        (previousLead &&
          current.air.flight_identification === (previousLead.callsign || ""));
      const departureWasAutomatic =
        !current.air.departure_location ||
        (previousLead &&
          current.air.departure_location === (previousLead.current_location || ""));
      return {
        ...current,
        air: {
          ...current.air,
          air_asset_ids: ids,
          air_pilots: pilots,
          air_copilots: copilots,
          air_asset: lead,
          departure_location:
            asset && departureWasAutomatic
              ? asset.current_location || ""
              : current.air.departure_location,
          aircraft_type: asset ? asset.model || "" : current.air.aircraft_type,
          aircraft_tail_number: asset
            ? asset.tail_number || ""
            : current.air.aircraft_tail_number,
          flight_identification:
            asset && callsignWasAutomatic
              ? asset.callsign || ""
              : current.air.flight_identification,
        },
      };
    });
  }

  function moveAirSlot(from, to) {
    if (!Number.isInteger(from) || from === to) return;
    setForm((current) => {
      const count = Math.max(1, Number(current.air.aircraft_count) || 1);
      if (from < 0 || from >= count || to < 0 || to >= count) return current;
      const ids = Array.from(
        { length: count },
        (_, position) => current.air.air_asset_ids[position] || ""
      );
      const pilots = Array.from(
        { length: count },
        (_, position) => current.air.air_pilots?.[position] || ""
      );
      const copilots = Array.from(
        { length: count },
        (_, position) => current.air.air_copilots?.[position] || ""
      );
      const [moved] = ids.splice(from, 1);
      ids.splice(to, 0, moved);
      const [movedPilot] = pilots.splice(from, 1);
      pilots.splice(to, 0, movedPilot);
      const [movedCopilot] = copilots.splice(from, 1);
      copilots.splice(to, 0, movedCopilot);
      // The lead is an aircraft identity, not a slot: moving it in the order
      // keeps the same lead selected and recomputes the detail sequence.
      return {
        ...current,
        air: {
          ...current.air,
          air_asset_ids: ids,
          air_pilots: pilots,
          air_copilots: copilots,
        },
      };
    });
  }

  // The pickers refuse an end before its start, but a start moved past an
  // already-picked end leaves that end behind; say so instead of letting the
  // server reject it after submission.
  const endBeforeStart = Boolean(
    form.ground.estimated_start &&
      form.ground.estimated_end &&
      form.ground.estimated_end < form.ground.estimated_start
  );

  // Offices/sections belong to the selected UPR: changing unit invalidates the
  // current choice instead of leaving a section from another unit attached.
  const offices = useApi(
    form.unit ? `/offices/${qs({ unit: form.unit, is_active: "true", page_size: 200 })}` : "",
    { enabled: canPlan && Boolean(form.unit), deps: [form.unit] }
  );
  const officeOptions = offices.data?.results ?? [];

  // Evaluators come from the personnel assigned to the mission's own unit and
  // must hold MRM evaluation authority there; both rules are enforced server-side.
  const evaluators = useApi(
    form.unit
      ? `/personnel/assignable/${qs({ unit: form.unit, purpose: "evaluator", page_size: 500 })}`
      : "",
    { enabled: canPlan && Boolean(form.unit), deps: [form.unit] }
  );
  // Same roster order as the personnel picker: senior rank first, then name.
  const evaluatorOptions = useMemo(
    () => [...(evaluators.data?.results ?? [])].sort(byRank),
    [evaluators.data]
  );

  // Eligible approving authorities for the selected unit. The roster comes from
  // live appointments, so a post with no holder simply offers nobody.
  const authorities = useApi(
    form.unit && isGround
      ? `/missions/approving-authorities/${qs({ unit: form.unit })}`
      : "",
    { enabled: canPlan && isGround && Boolean(form.unit), deps: [form.unit, isGround] }
  );
  const postOptions = authorities.data?.roles?.length ? authorities.data.roles : APPROVING_POSTS;
  const authorityOptions = (authorities.data?.results ?? []).filter(
    (row) => !authorityRole || row.role === authorityRole
  );
  const signatoryOptions = authorities.data?.results ?? [];
  const routePreview = authorityRole
    ? ROUTE_ROLE_ORDER.slice(0, ROUTE_ROLE_ORDER.indexOf(authorityRole) + 1)
    : [];
  const higherPreview = authorityRole
    ? ROUTE_ROLE_ORDER.slice(ROUTE_ROLE_ORDER.indexOf(authorityRole) + 1)
    : [];

  useEffect(() => {
    if (form.unit && !unitOptions.some((unit) => String(unit.id) === String(form.unit))) {
      setForm((current) => ({ ...current, unit: "" }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [units.data]);

  useEffect(() => {
    // Default the UPR to the operator's own unit once the scoped list arrives.
    if (canPlan && !form.unit && user?.unit) {
      setForm((current) => ({ ...current, unit: String(user.unit) }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canPlan, user?.unit]);

  // A trip keeps the mission's own window unless the planner sets its dates.
  useEffect(() => {
    if (!tripOpen) return;
    setTrip((current) => {
      const departure = current.departure_at || form.ground.estimated_start || "";
      const back = current.return_at || form.ground.estimated_end || "";
      if (departure === current.departure_at && back === current.return_at) return current;
      return { ...current, departure_at: departure, return_at: back };
    });
  }, [tripOpen, form.ground.estimated_start, form.ground.estimated_end]);

  // Stop 2 is the mission's operational area. If the Location is pinned after
  // the vehicles are selected, fill the blank final row rather than overwriting
  // a stop the planner already described.
  useEffect(() => {
    if (!form.latitude || !form.longitude) return;
    setTripPlans((current) => {
      let changed = false;
      const next = {};
      for (const [id, stops] of Object.entries(current)) {
        next[id] = stops;
        const last = stops[stops.length - 1];
        if (!last || last.location_name || last.latitude || last.longitude) continue;
        changed = true;
        next[id] = stops.map((stop, index) =>
          index === stops.length - 1
            ? {
                ...stop,
                location_name: form.location_name || "Operational area",
                mgrs: form.mgrs || "",
                latitude: String(form.latitude),
                longitude: String(form.longitude),
                remarks: "Destination — mission operational area",
              }
            : stop
        );
      }
      return changed ? next : current;
    });
  }, [form.latitude, form.longitude, form.location_name, form.mgrs]);

  // Whenever the pinned area or the UPR changes, ask the server whether the
  // point left the unit's own AOR and which unit owns the destination AOR. A
  // stale answer must never overwrite a newer one, so a superseded run ignores
  // its result. The request is debounced: typing a coordinate is one lookup.
  useEffect(() => {
    if (!form.unit || !form.latitude || !form.longitude) {
      setAorHandoff(null);
      return undefined;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const body = await api.get(
          `/gis/pin/${qs({ lat: form.latitude, lon: form.longitude, unit: form.unit })}`
        );
        if (cancelled) return;
        setAorHandoff(body.aor_handoff || null);
        // A new area is a new question: ask again rather than keeping an old
        // "notify" answer for a different destination unit.
        setNotifyDestination(true);
      } catch {
        if (!cancelled) setAorHandoff(null);
      }
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [form.unit, form.latitude, form.longitude]);

  // The personnel picker reads like a roster: senior rank first, then name.
  const personOptions = useMemo(
    () => [...(people.data?.results ?? [])].sort(byRank),
    [people.data]
  );
  const vehicleOptions = vehicles.data?.results ?? [];

  const involvedOptions = useMemo(
    () => personnel.filter((row) => row.user_id),
    [personnel]
  );

  /** A vehicle always reads MAKE MODEL PLATE — KIA KM450 12389. */
  function vehicleLabel(vehicle) {
    if (!vehicle) return "";
    if (vehicle.display_name) return vehicle.display_name;
    const make = vehicle.make || vehicle.make_model || "";
    const model = vehicle.model || vehicle.vehicle_type || "";
    return [make, model, vehicle.registration_number].filter(Boolean).join(" ");
  }

  /** The name exactly as it reads on the roster: rank, name, then serial. */
  function personLabel(userId) {
    const person = personOptions.find((item) => item.id === Number(userId));
    return person ? `${person.full_name}-${person.serial_number}` : `Personnel #${userId}`;
  }

  /** A custom designation is the visible role wherever the roster is summarized. */
  function personRoleLabel(row) {
    if (row.role === "Specify") return (row.custom_role || "").trim() || "Specify";
    return row.role || "Member";
  }

  // People already on the mission are not offered again, so the picker can
  // never add the same person twice.
  const pickerOptions = useMemo(() => {
    const chosen = new Set(personnel.map((row) => String(row.user_id)));
    return personOptions.filter((person) => !chosen.has(String(person.id)));
  }, [personOptions, personnel]);

  const filteredPickerOptions = useMemo(() => {
    const query = personQuery.trim().toLowerCase();
    if (!query) return pickerOptions;
    return pickerOptions.filter((person) =>
      `${person.full_name} ${person.serial_number}`.toLowerCase().includes(query)
    );
  }, [pickerOptions, personQuery]);

  function openPersonPicker() {
    setPersonQuery("");
    setPersonPicks([]);
    setPersonPickerOpen(true);
  }

  function togglePersonPick(id) {
    setPersonPicks((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id]
    );
  }

  function addPickedPeople() {
    const chosen = pickerOptions.filter((person) => personPicks.includes(person.id));
    if (!chosen.length) return;
    setPersonnel((current) => [
      ...current,
      ...chosen.map((person) => ({ user_id: String(person.id), role: "Member" })),
    ]);
    setPersonPickerOpen(false);
  }

  // Roster rows as searchable driver choices. The visible label is the rank and
  // name; the serial is the second line and both are matched when filtering.
  const driverChoices = useMemo(
    () =>
      involvedOptions
        .map((row) => {
          const person = personOptions.find((item) => item.id === Number(row.user_id));
          return {
            value: String(row.user_id),
            label: person
              ? `${person.full_name} — ${person.serial_number}`
              : `Personnel #${row.user_id}`,
            hint: personRoleLabel(row),
            person,
          };
        })
        .sort((left, right) => byRank(left.person, right.person))
        .filter((option, index, rows) => rows.findIndex((row) => row.value === option.value) === index)
        .map(({ person, ...option }) => option),
    [involvedOptions, personOptions]
  );

  // People already designated as Driver on the mission roster. A single one
  // can be filled in automatically when the trip uses one vehicle; with two or
  // more vehicles the planner assigns a driver to each.
  const designatedDriverIds = useMemo(
    () =>
      involvedOptions
        .filter((row) => row.role === "Driver")
        .map((row) => String(row.user_id)),
    [involvedOptions]
  );

  useEffect(() => {
    if (!tripOpen) return;
    const planned = Object.keys(tripPlans).map(Number);
    if (planned.length !== 1 || designatedDriverIds.length !== 1) return;
    const vehicleId = planned[0];
    const driverId = designatedDriverIds[0];
    setTripDrivers((current) => {
      const currentDriver = current[vehicleId] ? String(current[vehicleId]) : "";
      if (currentDriver === driverId) return current;
      // A person the planner picked by hand is never overwritten; only an empty
      // or stale designation is filled from the roster.
      if (
        currentDriver &&
        involvedOptions.some((row) => String(row.user_id) === currentDriver)
      ) {
        return current;
      }
      return { ...current, [vehicleId]: driverId };
    });
  }, [tripOpen, tripPlans, designatedDriverIds, involvedOptions]);

  function setField(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function setDetail(section, key, value) {
    setForm((current) => ({ ...current, [section]: { ...current[section], [key]: value } }));
  }

  function changeUnit(value) {
    setForm((current) => ({
      ...current,
      unit: value,
      office_section: "",
    }));
    // Selections tied to the previous unit no longer apply.
    setMrmCreator("");
    setMrmEvaluator("");
    setAuthorityRole("");
    setAuthorityUser("");
  }

  function setStops(vehicleId, updater) {
    setTripPlans((current) => ({
      ...current,
      [vehicleId]: updater(current[vehicleId] || [{ ...EMPTY_STOP }]),
    }));
  }

  /** The closing row is an origin when the trip was closed with Return to origin. */
  function returnsToOrigin(stop) {
    if (!stop) return false;
    if (stop.return_to_origin) return true;
    return (stop.remarks || "").trim().toLowerCase() === "return to origin";
  }

  /** Remarks are positional, so they stay right when a row is added or moved. */
  function itineraryRemark(index, stops) {
    const lastIndex = stops.length - 1;
    const destinationIndex = returnsToOrigin(stops[lastIndex]) ? lastIndex - 1 : lastIndex;
    if (index === 0) return "Origin";
    if (index === destinationIndex) return "Destination";
    if (index === lastIndex) return "Origin";
    return `Itinerary ${index}`;
  }

  function updateStop(index, key, value) {
    if (activeVehicle === null) return;
    setStops(activeVehicle, (stops) =>
      stops.map((stop, i) => (i === index ? { ...stop, [key]: value } : stop))
    );
  }

  function addStop() {
    if (activeVehicle === null) return;
    // The seeded final row is the mission area, so a new en-route stop belongs
    // between it and the origin.
    setStops(activeVehicle, (stops) => {
      if (stops.length < 2) return [...stops, { ...EMPTY_STOP }];
      return [...stops.slice(0, -1), { ...EMPTY_STOP }, stops[stops.length - 1]];
    });
  }

  function removeStop(index) {
    if (activeVehicle === null) return;
    setStops(activeVehicle, (stops) => stops.filter((_, i) => i !== index));
  }

  /** Reorder a vehicle's itinerary without changing any stop details. */
  function moveStop(from, to) {
    if (activeVehicle === null || from === to) return;
    setStops(activeVehicle, (stops) => {
      if (from < 0 || to < 0 || from >= stops.length || to >= stops.length) return stops;
      const next = [...stops];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }

  /**
   * Stop 1 defaults to the vehicle's unit location and stop 2 to the mission's
   * operational area. The planner only has to add the points in between.
   */
  function newItinerary(vehicleId) {
    const vehicle = vehicleOptions.find((row) => row.id === Number(vehicleId));
    const pin = unitPinById.get(Number(vehicle?.unit)) || unitPinById.get(Number(form.unit));
    const origin = pin
      ? {
          location_name: pin.place_name || pin.unit_acronym || "Unit location",
          mgrs: pin.mgrs || "",
          latitude: Number(pin.latitude).toFixed(6),
          longitude: Number(pin.longitude).toFixed(6),
          remarks: `Origin — ${pin.unit_acronym || "unit location"}`,
        }
      : { ...EMPTY_STOP, remarks: "Origin" };

    const destination =
      form.latitude && form.longitude
        ? {
            location_name: form.location_name || "Operational area",
            mgrs: form.mgrs || "",
            latitude: String(form.latitude),
            longitude: String(form.longitude),
            remarks: "Destination — mission operational area",
          }
        : { ...EMPTY_STOP, remarks: "Destination" };

    return [origin, destination];
  }

  /** Close the trip with the origin again, so a round trip needs no map work. */
  function addReturnToOrigin() {
    if (activeVehicle === null) return;
    const origin = (tripPlans[activeVehicle] || [])[0];
    if (
      !origin ||
      (!(origin.location_name || "").trim() && !origin.mgrs && !origin.latitude)
    ) {
      setError("Enter the origin in stop 1 before adding a return to origin.");
      return;
    }
    setError("");
    setStops(activeVehicle, (stops) => {
      const start = stops[0];
      return [
        ...stops,
        {
          ...start,
          return_to_origin: true,
          remarks: "Return to origin",
          location_name: start.location_name
            ? `${start.location_name} (return)`
            : "Return to origin",
        },
      ];
    });
  }

  function toggleTrip() {
    if (!tripOpen) {
      // A vehicle trip runs inside the mission's own window by default.
      setTrip((current) => ({
        ...current,
        departure_at: current.departure_at || form.ground.estimated_start || "",
        return_at: current.return_at || form.ground.estimated_end || "",
      }));
    }
    setTripOpen(!tripOpen);
  }

  function removeVehicle(id) {
    const next = { ...tripPlans };
    delete next[id];
    setTripPlans(next);
    setTripDrivers((current) => {
      const drivers = { ...current };
      delete drivers[id];
      return drivers;
    });
    setActiveVehicle(Object.keys(next)[0] ?? null);
  }

  /** How a plan key reads in the itinerary tabs and in validation messages. */
  function planLabel(key) {
    if (key.startsWith(POV_PREFIX)) {
      const pov = povs.find((row) => `${POV_PREFIX}${row.ref}` === key);
      if (!pov) return "POV";
      const owner = driverChoices.find((option) => option.value === String(pov.owner));
      const description = [pov.make_model || pov.vehicle_type || "vehicle", pov.plate_number]
        .filter(Boolean)
        .join(" ");
      return `POV ${description}${owner ? ` — ${owner.label}` : ""}`;
    }
    const vehicle = vehicleOptions.find((row) => row.id === Number(key));
    return vehicle ? vehicleLabel(vehicle) : `Vehicle ${key}`;
  }

  function addPov() {
    const ref = String(povSeq + 1);
    setPovSeq(povSeq + 1);
    setPovs((current) => [
      ...current,
      {
        ref,
        vehicle_type: "",
        make_model: "",
        plate_number: "",
        fuel_type: "Gasoline",
        owner: "",
        driver: "",
      },
    ]);
    setTripPlans((current) => ({
      ...current,
      [`${POV_PREFIX}${ref}`]: newItinerary(null),
    }));
    setActiveVehicle(`${POV_PREFIX}${ref}`);
  }

  function updatePov(ref, key, value) {
    setPovs((current) =>
      current.map((row) => {
        if (row.ref !== ref) return row;
        const next = { ...row, [key]: value };
        // The owner drives their own vehicle unless somebody else is named.
        if (key === "owner" && (!row.driver || row.driver === row.owner)) next.driver = value;
        return next;
      })
    );
    if (key === "owner") {
      setTripDrivers((current) => {
        const planningKey = `${POV_PREFIX}${ref}`;
        const current_driver = current[planningKey] || "";
        const pov = povs.find((row) => row.ref === ref);
        if (current_driver && pov && current_driver !== pov.owner) return current;
        return { ...current, [planningKey]: value };
      });
    }
  }

  function removePov(ref) {
    const key = `${POV_PREFIX}${ref}`;
    setPovs((current) => current.filter((row) => row.ref !== ref));
    setTripPlans((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setTripDrivers((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setActiveVehicle((current) => (String(current) === key ? null : current));
  }

  /** A driver is only valid while they are still one of the mission's people. */
  function driverIsInvolved(vehicleId) {
    const driver = tripDrivers[vehicleId];
    return Boolean(driver) && involvedOptions.some((row) => String(row.user_id) === String(driver));
  }

  function driverChoicesFor(vehicleId) {
    // One person cannot drive two vehicles on the same trip, so a driver already
    // named for another selected vehicle is not offered here.
    const used = new Set(
      Object.entries(tripDrivers)
        .filter(
          ([id, userId]) =>
            Number(id) !== Number(vehicleId) && userId && tripPlans[id]
        )
        .map(([, userId]) => String(userId))
    );
    return driverChoices.filter((option) => !used.has(option.value));
  }

  function chooseDriver(vehicleId, userId) {
    setTripDrivers((current) => {
      const next = { ...current };
      if (userId) next[vehicleId] = userId;
      else delete next[vehicleId];
      return next;
    });
    // Clearing the driver removes the vehicle too: a planned route must never
    // outlive the person who was designated to drive it.
    if (!userId && tripPlans[vehicleId]) removeVehicle(vehicleId);
  }

  function toggleVehicle(id) {
    if (tripPlans[id]) {
      removeVehicle(id);
      return;
    }
    // The first vehicle may be ticked using the single designated Driver from
    // the roster; the effect above fills that driver in. With any vehicle
    // already planned, its own driver must be chosen first.
    const autoDriverReady =
      designatedDriverIds.length === 1 && Object.keys(tripPlans).length === 0;
    if (!driverIsInvolved(id) && !autoDriverReady) return;
    setTripPlans((current) => ({ ...current, [id]: newItinerary(id) }));
    setActiveVehicle(id);
  }

  async function resolveMissionCoordinates() {
    try {
      const payload = form.mgrs
        ? { mgrs: form.mgrs }
        : { latitude: Number(form.latitude), longitude: Number(form.longitude) };
      const result = await api.post("/gis/mgrs/", payload);
      setForm((current) => ({
        ...current,
        mgrs: result.mgrs,
        latitude: result.latitude.toFixed(6),
        longitude: result.longitude.toFixed(6),
      }));
    } catch (err) {
      setError(err.message);
    }
  }

  /** Reuse a saved operational-mapping feature for the mission or one stop. */
  function openOperationalPicker(target = null) {
    setOperationalTarget(target);
    setOperationalPickerOpen(true);
  }

  function applyLocation(picked, override) {
    const target = override !== undefined ? override : locationTarget;
    if (target && typeof target === "object" && target.kind === "stop") {
      setStops(target.vehicleId, (stops) =>
        stops.map((stop, position) =>
          position === target.index
            ? {
                ...stop,
                location_name: picked.location_name || stop.location_name,
                latitude: picked.latitude,
                longitude: picked.longitude,
                mgrs: picked.mgrs,
              }
            : stop
        )
      );
      setLocationTarget(null);
      return;
    }
    if (target && typeof target === "object" && target.kind === "additional") {
      setForm((current) => {
        const rows = [...current.ground.additional_areas];
        rows[target.index] = {
          name: picked.location_name || rows[target.index]?.name || "",
          latitude: picked.latitude,
          longitude: picked.longitude,
          mgrs: picked.mgrs,
        };
        return { ...current, ground: { ...current.ground, additional_areas: rows } };
      });
      setLocationTarget(null);
      return;
    }
    setForm((current) => ({
      ...current,
      location_name: picked.location_name || current.location_name,
      latitude: picked.latitude,
      longitude: picked.longitude,
      mgrs: picked.mgrs,
      ground: {
        ...current.ground,
        operational_area:
          current.category === "ground"
            ? picked.location_name || current.ground.operational_area
            : current.ground.operational_area,
      },
    }));
    setLocationTarget(null);
  }

  function addAdditionalArea() {
    setForm((current) => {
      const rows = [...current.ground.additional_areas, { name: "", latitude: "", longitude: "", mgrs: "" }];
      return { ...current, ground: { ...current.ground, additional_areas: rows } };
    });
    setLocationTarget({ kind: "additional", index: form.ground.additional_areas.length });
  }

  function updateAdditionalArea(index, key, value) {
    setForm((current) => {
      const rows = current.ground.additional_areas.map((row, position) =>
        position === index ? { ...row, [key]: value } : row
      );
      return { ...current, ground: { ...current.ground, additional_areas: rows } };
    });
  }

  function removeAdditionalArea(index) {
    setForm((current) => ({
      ...current,
      ground: {
        ...current.ground,
        additional_areas: current.ground.additional_areas.filter(
          (_, position) => position !== index
        ),
      },
    }));
  }

  function buildTeam() {
    const team = [];
    if (mrmCreator) team.push({ user: Number(mrmCreator), role: "lead" });
    if (mrmEvaluator) team.push({ user: Number(mrmEvaluator), role: "reviewer" });
    return team;
  }

  function updateAirElement(index, field, value) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position === index ? { ...element, [field]: value } : element
        ),
      },
    }));
  }

  function addAirElement() {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: [
          ...current.air.elements,
          { mission: "", aircraft: [emptyAircraft(true)] },
        ],
      },
    }));
  }

  function removeAirElement(index) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.filter((_, position) => position !== index),
      },
    }));
  }

  function updateAirAircraft(elementIndex, aircraftIndex, field, value) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position !== elementIndex
            ? element
            : {
                ...element,
                aircraft: element.aircraft.map((aircraft, aircraftPosition) =>
                  aircraftPosition === aircraftIndex
                    ? { ...aircraft, [field]: value }
                    : aircraft
                ),
              }
        ),
      },
    }));
  }

  function addAirAircraft(elementIndex) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position === elementIndex
            ? { ...element, aircraft: [...element.aircraft, emptyAircraft(false)] }
            : element
        ),
      },
    }));
  }

  function removeAirAircraft(elementIndex, aircraftIndex) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position === elementIndex
            ? {
                ...element,
                aircraft: element.aircraft.filter(
                  (_, itemPosition) => itemPosition !== aircraftIndex
                ),
              }
            : element
        ),
      },
    }));
  }

  function updateAirCrew(elementIndex, aircraftIndex, crewIndex, field, value) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position !== elementIndex
            ? element
            : {
                ...element,
                aircraft: element.aircraft.map((aircraft, aircraftPosition) =>
                  aircraftPosition !== aircraftIndex
                    ? aircraft
                    : {
                        ...aircraft,
                        crew: aircraft.crew.map((row, crewPosition) =>
                          crewPosition === crewIndex
                            ? { ...row, [field]: value }
                            : row
                        ),
                      }
                ),
              }
        ),
      },
    }));
  }

  function addAirCrew(elementIndex, aircraftIndex) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position === elementIndex
            ? {
                ...element,
                aircraft: element.aircraft.map((aircraft, aircraftPosition) =>
                  aircraftPosition === aircraftIndex
                    ? {
                        ...aircraft,
                        crew: [...aircraft.crew, { role: "CP", name: "" }],
                      }
                    : aircraft
                ),
              }
            : element
        ),
      },
    }));
  }

  function removeAirCrew(elementIndex, aircraftIndex, crewIndex) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position === elementIndex
            ? {
                ...element,
                aircraft: element.aircraft.map((aircraft, aircraftPosition) =>
                  aircraftPosition === aircraftIndex
                    ? {
                        ...aircraft,
                        crew: aircraft.crew.filter(
                          (_, itemPosition) => itemPosition !== crewIndex
                        ),
                      }
                    : aircraft
                ),
              }
            : element
        ),
      },
    }));
  }

  function updateAirLeg(elementIndex, aircraftIndex, legIndex, field, value) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position !== elementIndex
            ? element
            : {
                ...element,
                aircraft: element.aircraft.map((aircraft, aircraftPosition) =>
                  aircraftPosition !== aircraftIndex
                    ? aircraft
                    : {
                        ...aircraft,
                        itineraries: aircraft.itineraries.map((leg, legPosition) =>
                          legPosition === legIndex
                            ? { ...leg, [field]: value }
                            : leg
                        ),
                      }
                ),
              }
        ),
      },
    }));
  }

  function addAirLeg(elementIndex, aircraftIndex) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position === elementIndex
            ? {
                ...element,
                aircraft: element.aircraft.map((aircraft, aircraftPosition) =>
                  aircraftPosition === aircraftIndex
                    ? {
                        ...aircraft,
                        itineraries: [
                          ...aircraft.itineraries,
                          {
                            etd: "",
                            eta: "",
                            origin: "",
                            destination: "",
                            route: "",
                            mission: "",
                            point_role: "",
                            overflight: false,
                            return_to_origin: false,
                          },
                        ],
                      }
                    : aircraft
                ),
              }
            : element
        ),
      },
    }));
  }

  function removeAirLeg(elementIndex, aircraftIndex, legIndex) {
    setForm((current) => ({
      ...current,
      air: {
        ...current.air,
        elements: current.air.elements.map((element, position) =>
          position === elementIndex
            ? {
                ...element,
                aircraft: element.aircraft.map((aircraft, aircraftPosition) =>
                  aircraftPosition === aircraftIndex
                    ? {
                        ...aircraft,
                        itineraries: aircraft.itineraries.filter(
                          (_, itemPosition) => itemPosition !== legIndex
                        ),
                      }
                    : aircraft
                ),
              }
            : element
        ),
      },
    }));
  }

  function buildAirItineraryChanges() {
    return (form.air.elements || []).flatMap((element) => {
      const lead =
        (element.aircraft || []).find((aircraft) => aircraft.is_lead) ||
        element.aircraft?.[0];
      return (lead?.itineraries || [])
        .filter(
          (leg) =>
            leg.id &&
            !(
              leg.atd ||
              leg.ata ||
              leg.tot ||
              leg.toot ||
              leg.status === "cancelled"
            )
        )
        .map((leg) => ({
          id: Number(leg.id),
          etd: toApiDateTime(leg.etd),
          eta: toApiDateTime(leg.eta),
          origin: leg.origin || "",
          destination: leg.destination || "",
          route: leg.route || "",
          mission: leg.mission || "",
          point_role: leg.point_role || "",
          overflight: Boolean(leg.overflight),
          return_to_origin: Boolean(leg.return_to_origin),
        }));
    });
  }

  function buildPayload() {
    // Air operations carry no UPR pick, O/SPR pick, mission location or MRM:
    // the unit is the planner's own, the Operations Center is responsible, and
    // the flight's own detail replaces a plotted location.
    const air = form.category === "air";
    const generatedAirTitle = form.air.operation_date
      ? `Flight Operations ${form.air.operation_date}`
      : "Flight Operations";
    const payload = {
      category: form.category,
      title: air
        ? form.title.trim() || generatedAirTitle
        : form.title.trim(),
      purpose: air ? "" : form.purpose,
      unit: Number(air ? user?.unit || form.unit : form.unit) || null,
      office_section: air ? "Operations Center" : form.office_section,
      location_name: air ? "" : form.location_name,
      latitude: air || form.latitude === "" ? null : Number(form.latitude),
      longitude: air || form.longitude === "" ? null : Number(form.longitude),
      mgrs: air ? "" : form.mgrs,
      remarks: form.remarks,
      status: "planned",
    };
    if (!isGround) {
      payload.depends_on = form.depends_on ? Number(form.depends_on) : null;
      payload.dependency_release = form.dependency_release;
      payload.dependency_buffer_minutes =
        Number(form.dependency_buffer_minutes) || 0;
    }
    if (isGround) {
      payload.ground_detail = {
        // Philippine wall-clock time; the API stores it as entered.
        estimated_start: toApiDateTime(form.ground.estimated_start),
        estimated_end: toApiDateTime(form.ground.estimated_end),
        upr_unit: form.unit ? Number(form.unit) : null,
        osp_section: form.office_section,
        operational_area: form.ground.operational_area || form.location_name,
        ground_risk_notes: form.ground.ground_risk_notes,
      };
      // Only the extra areas the planner actually added are sent.
      payload.operational_areas = form.ground.additional_areas
        .filter((row) => row.name.trim())
        .map((row) => ({
          name: row.name.trim(),
          latitude: row.latitude === "" ? null : Number(row.latitude),
          longitude: row.longitude === "" ? null : Number(row.longitude),
          mgrs: row.mgrs || "",
        }));
      if (aorHandoff && notifyDestination) {
        // The server re-checks the AOR itself, so a stale checkbox can never
        // notify an unrelated unit.
        payload.notify_destination_aor = true;
      }
    } else {
      const elements = form.air.elements || [];
      const aircraftRows = elements.flatMap((element) => element.aircraft || []);
      const legs = aircraftRows.flatMap((aircraft) => aircraft.itineraries || []);
      const firstAircraft = aircraftRows[0] || {};
      const routeForLeg = (leg) =>
        [leg.origin, leg.destination].filter(Boolean).join("-");
      const firstLeg = legs.find((leg) => leg.origin || leg.destination) || legs[0] || {};
      const lastLeg = legs[legs.length - 1] || firstLeg;
      const operationTime = normalizeTimeInput(form.air.operation_time);
      const operationEtd =
        form.air.operation_date && operationTime
          ? toApiDateTime(`${form.air.operation_date}T${operationTime}`)
          : null;
      payload.air_detail = {
        operation_date: form.air.operation_date || null,
        mission_configuration: aircraftRows.length === 1 ? "single" : "element",
        aircraft_count: Math.max(1, aircraftRows.length),
        aircraft_type: firstAircraft.aircraft_type || "",
        aircraft_tail_number: firstAircraft.tail_number || "",
        flight_identification:
          firstAircraft.callsign || firstAircraft.tail_number || "",
        departure_location: firstLeg.origin || routeForLeg(firstLeg),
        destination_location: lastLeg.destination || routeForLeg(lastLeg),
        route: legs.map(routeForLeg).filter(Boolean).join("\n"),
        scheduled_departure: operationEtd,
        scheduled_arrival: null,
        elements: elements.map((element) => ({
          mission: element.mission || "",
          aircraft: (element.aircraft || []).map((aircraft, aircraftIndex) => ({
            aircraft_type: aircraft.aircraft_type || "",
            tail_number: aircraft.tail_number || "",
            callsign: aircraft.callsign || "",
            is_lead: Boolean(aircraft.is_lead || aircraftIndex === 0),
            crew: (aircraft.crew || [])
              .filter((row) => (row.name || "").trim())
              .map((row) => ({
                role: row.role || "PIC",
                name: row.name.trim(),
              })),
            itineraries: (aircraft.itineraries || []).map(
              (leg, legIndex, allLegs) => {
                const origin =
                  legIndex === 0
                    ? leg.origin || ""
                    : allLegs[legIndex - 1]?.destination || "";
                return {
                  etd:
                    legIndex === 0
                      ? operationEtd || toApiDateTime(leg.etd)
                      : toApiDateTime(leg.etd),
                  eta: toApiDateTime(leg.eta),
                  atd: toApiDateTime(leg.atd),
                  ata: toApiDateTime(leg.ata),
                  tot: toApiDateTime(leg.tot),
                  toot: toApiDateTime(leg.toot),
                  status: leg.status || "planned",
                  origin,
                  destination: leg.destination || "",
                  route: [origin, leg.destination].filter(Boolean).join("-"),
                  mission: leg.mission || "",
                  point_role: leg.point_role || "",
                  overflight: Boolean(leg.overflight),
                  return_to_origin: Boolean(leg.return_to_origin),
                };
              }
            ),
          })),
        })),
      };
    }
    return payload;
  }

  function buildStops(stops) {
    return (stops || [])
      .filter((stop) => stop.location_name.trim())
      .map((stop, index, locatedStops) => ({
        sequence: index + 1,
        location_name: stop.location_name.trim(),
        mgrs: stop.mgrs || "",
        latitude: stop.latitude === "" ? null : Number(stop.latitude),
        longitude: stop.longitude === "" ? null : Number(stop.longitude),
        remarks: itineraryRemark(index, locatedStops),
      }));
  }

  async function saveVehicles(mission, warnings) {
    const planKeys = Object.keys(tripPlans);
    if (!planKeys.length) return;

    const inventoryKeys = planKeys.filter((key) => !key.startsWith(POV_PREFIX));
    const drivers = Object.fromEntries(
      inventoryKeys.map((key) => [Number(key), Number(tripDrivers[key])])
    );
    // A POV does not exist as a vehicle until this call: the Operations Center's
    // description travels with the assignment, and the server returns the trip
    // that was opened for it.
    const povPayload = planKeys
      .filter((key) => key.startsWith(POV_PREFIX))
      .map((key) => povs.find((row) => `${POV_PREFIX}${row.ref}` === key))
      .filter(Boolean)
      .map((row) => ({
        client_ref: `${POV_PREFIX}${row.ref}`,
        vehicle_type: row.vehicle_type.trim(),
        make_model: row.make_model.trim(),
        plate_number: row.plate_number.trim(),
        fuel_type: row.fuel_type,
        owner: Number(row.owner),
        driver: Number(row.driver || row.owner),
      }));

    const assigned = await api.post(`/missions/${mission.id}/vehicles/`, {
      vehicles: inventoryKeys.map(Number),
      drivers,
      povs: povPayload,
    });

    const trips = [
      ...(assigned.vehicles || []).map((row) => ({
        key: String(row.vehicle_id),
        tripId: row.trip_id,
        name: row.registration_number,
      })),
      ...(assigned.pov_vehicles || []).map((row) => ({
        key: row.client_ref,
        tripId: row.trip_id,
        name: row.registration_number,
      })),
    ];

    for (const row of trips) {
      if (!row.tripId) continue;
      const stops = buildStops(tripPlans[row.key]);
      if (!stops.length) continue;

      await api.patch(`/trips/${row.tripId}/`, {
        // The trip is named after the mission unless the planner renamed it.
        title: trip.title.trim() || form.title.trim() || "Mission vehicle movement",
        purpose: form.purpose,
        departure_at: toApiDateTime(trip.departure_at),
        return_at: toApiDateTime(trip.return_at),
        remarks: trip.remarks,
      });
      await api.put(`/trips/${row.tripId}/itinerary/`, { stops });

      const located = stops.filter(
        (stop) => stop.latitude !== null && stop.longitude !== null
      );
      if (located.length < 2) continue;
      try {
        await api.post(`/trips/${row.tripId}/route/`, {});
      } catch (routeError) {
        // The itinerary saved; the road route did not. Say so instead of
        // pretending a route exists.
        warnings.push(
          `${row.name}: the road route could not be calculated — ${routeError.message}`
        );
      }
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setErrors({});
    try {
      if (airMissionLocked) {
        const legs = buildAirItineraryChanges();
        if (!legs.length) {
          setError("No unflown itinerary legs remain to change.");
          return;
        }
        const invalid = legs.find(
          (leg) => leg.etd && leg.eta && leg.eta <= leg.etd
        );
        if (invalid) {
          setError("Every ETA must be after its ETD before saving.");
          return;
        }
        await api.post(`/missions/${editId}/change-itinerary/`, { legs });
        navigate("/missions/air", {
          replace: true,
          state: {
            itinerarySaved: true,
            missionNumber: existing.data?.mission_number || "",
            airDate: form.air.operation_date || "",
          },
        });
        return;
      }
      if (!isGround) {
        const operationTimeRaw = (form.air.operation_time || "").trim();
        const operationTime = normalizeTimeInput(operationTimeRaw);
        if (operationTimeRaw && !operationTime) {
          setError("Enter the ETD as HH:MM in 24-hour Philippine Standard Time.");
          return;
        }
        if (
          form.air.operation_date === todayInput() &&
          operationTime &&
          operationTime < currentTimeManila()
        ) {
          setError(
            "ETD cannot be earlier than the current time for today's Flight Operations sheet."
          );
          return;
        }
        const elements = form.air.elements || [];
        const aircraftRows = elements.flatMap((element) => element.aircraft || []);
        if (!elements.length || !aircraftRows.length) {
          setError("Add at least one element and one aircraft before saving.");
          return;
        }
        if (!aircraftRows.some((row) => (row.aircraft_type || "").trim())) {
          setErrors({ "air_detail.aircraft_type": "Enter the aircraft type." });
          setError("Every element needs at least one aircraft type.");
          return;
        }

        const operationEtd =
          form.air.operation_date && operationTime
            ? `${form.air.operation_date}T${operationTime}`
            : "";
        const invalidEta = aircraftRows
          .flatMap((row) => row.itineraries || [])
          .find((leg) => leg.eta && operationEtd && leg.eta <= operationEtd);
        if (invalidEta) {
          setErrors({
            air_detail: "Every ETA must be after the mission ETD.",
          });
          setError("Set every ETA after the mission ETD before saving.");
          return;
        }

        const seenTails = new Set();
        for (const row of aircraftRows) {
          const tailNumber = (row.tail_number || "").trim();
          if (tailNumber && seenTails.has(tailNumber)) {
            setError(
              `Aircraft ${tailNumber} is selected more than once on this sheet.`
            );
            return;
          }
          if (tailNumber) seenTails.add(tailNumber);
        }

        for (const row of aircraftRows) {
          if (!row.aircraft_type || !row.tail_number) continue;
          const asset = airAssetOptions.find(
            (option) =>
              option.model === row.aircraft_type &&
              option.tail_number === row.tail_number
          );
          if (!asset) {
            setError(
              `Aircraft ${row.tail_number} is not in the Air Assets register.`
            );
            return;
          }
          if (asset.operational_status !== "available") {
            setError(
              `Aircraft ${row.tail_number} is not available (${asset.operational_status_label || asset.operational_status}).`
            );
            return;
          }
          const conflict = airConflictRows.find(
            (item) => String(item.air_asset) === String(asset.id)
          );
          if (conflict) {
            setError(
              `Aircraft ${row.tail_number} is already assigned to ${conflict.mission_number} in this time window.`
            );
            return;
          }
        }
      }

      const planKeys = Object.keys(tripPlans);
      if (isGround && planKeys.length) {
        const unfinishedPov = planKeys
          .filter((key) => key.startsWith(POV_PREFIX))
          .map((key) => povs.find((row) => `${POV_PREFIX}${row.ref}` === key))
          .find(
            (row) =>
              !row ||
              !row.vehicle_type.trim() ||
              !row.make_model.trim() ||
              !row.plate_number.trim() ||
              !row.fuel_type ||
              !row.owner
          );
        if (unfinishedPov) {
          setErrors({
            vehicles: "Describe every POV: vehicle type, model, plate number, fuel and owner.",
          });
          setError("Field the privately owned vehicle's details before saving.");
          return;
        }
        const withoutDriver = planKeys.filter((key) => !tripDrivers[key]);
        if (withoutDriver.length) {
          setErrors({
            vehicles: `Designate one driver for each vehicle: ${withoutDriver
              .map((key) => planLabel(key))
              .join(", ")}.`,
          });
          setError("Every vehicle needs its own designated driver before the mission can be saved.");
          return;
        }
        const assignedDrivers = planKeys.map((key) => String(tripDrivers[key]));
        if (new Set(assignedDrivers).size !== assignedDrivers.length) {
          setErrors({ vehicles: "A driver can only operate one vehicle on this trip." });
          setError("Each vehicle needs a different designated driver.");
          return;
        }
      }

      const missingDesignation = isGround
        ? personnel.find(
            (row) =>
              row.user_id && row.role === "Specify" && !(row.custom_role || "").trim()
          )
        : null;
      if (missingDesignation) {
        setErrors({ personnel: "Enter the custom designation for every person set to Specify." });
        setError("Enter each custom personnel designation before saving.");
        return;
      }

      const payload = buildPayload();
      if (editing && forceMrmUnlink) {
        // Explicit override from the revision form: drop the approved MRM so a
        // new assessment can be raised.
        payload.risk_form = null;
        payload.force_mrm_unlink = true;
      }
      if (editing) {
        // Revising must not reset the mission to draft or replay the AOR handoff.
        delete payload.status;
        delete payload.notify_destination_aor;
      }
      const mission = editing
        ? await api.patch(`/missions/${editId}/`, payload)
        : await api.post("/missions/", payload);

      if (isGround && personnel.length) {
        await api.post(`/missions/${mission.id}/assign-personnel/`, {
          personnel: personnel
            .filter((row) => row.user_id)
            .map((row) => ({
              user_id: Number(row.user_id),
              role: row.role || "Member",
              custom_role: row.role === "Specify" ? (row.custom_role || "").trim() : "",
            })),
        });
      }

      const team = buildTeam();
      if (isGround && team.length && !(editing && approvedMrm && !forceMrmUnlink)) {
        // The MRM team is assigned after the personnel are recorded: the server
        // checks that the creator is one of them.
        await api.patch(`/missions/${mission.id}/`, { mrm_team: team });
      }

      // The authority and signatory were fixed before routing. Re-posting them
      // while revising reopens approval at the decision authority instead of
      // returning the mission to the post that asked for the change.
      if (!editing && isGround && authorityRole && authorityUser) {
        await api.post(`/missions/${mission.id}/approving-authority/`, {
          role: authorityRole,
          user_id: Number(authorityUser),
          note: authorityNote,
        });
      }

      if (!editing && isGround && orderSignatory) {
        await api.post(`/missions/${mission.id}/mission-order/signatory/`, {
          user_id: Number(orderSignatory),
        });
      }

      const warnings = [];
      if (!editing && isGround && Object.keys(tripPlans).length) {
        try {
          await saveVehicles(mission, warnings);
        } catch (tripError) {
          // The mission exists; report the partial success honestly instead of
          // silently discarding the vehicles and itinerary.
          setCreatedMission(mission);
          setError(
            `Mission ${mission.mission_number} was created, but its vehicles and itinerary could not be saved: ${tripError.message}`
          );
          return;
        }
      }
      if (warnings.length) {
        setCreatedMission(mission);
        setError(
          `Mission ${mission.mission_number} was created. ${warnings.join(
            " "
          )} Open the mission to retry the route calculation.`
        );
        return;
      }

      if (editing && submitIntent.current === "resubmit") {
        await api.post(`/missions/${editId}/resubmit/`, {});
        refreshNotifications();
        navigate(`/missions/${editId}`);
        return;
      }
      if (editing) {
        // Back to the mission, where the resubmit offer names the post that
        // asked for the revision.
        refreshNotifications();
        navigate(`/missions/${editId}`, { state: { missionUpdated: true } });
        return;
      }
      navigate(`/missions/${mission.id}`);
    } catch (err) {
      setErrors(err.fieldErrors || {});
      setError(err.message || "Could not save the mission.");
    } finally {
      setBusy(false);
    }
  }

  if (!canPlan) {
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Plan operation</h1>
            <p>Mission planning belongs to A3 – Operations and the Operations Center office.</p>
          </div>
          <Link className="btn btn-ghost btn-md" to={`/missions/${form.category}`}>
            Back to missions
          </Link>
        </div>
        <Card>
          <EmptyState message="Only personnel assigned to A3 – Operations (or the unit's Operations Center office) may create or plan Air and Ground operations. Command and staff officers can review missions from the Missions page." />
        </Card>
      </>
    );
  }

  if (editing && existing.error) {
    return <ErrorBanner message={existing.error} onRetry={existing.reload} />;
  }

  if (editing && !prefilled) {
    return (
      <div className="page-head">
        <div>
          <h1>Revise mission</h1>
          <p>Loading the mission…</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>
            {airMissionLocked
              ? "Change flight itinerary"
              : editing
                ? "Revise mission"
                : "Plan operation"}
          </h1>
          <p>
            {airMissionLocked
              ? "Actual times exist, so finished legs, aircraft, crew, callsign and the flight schedule are locked. Only unflown itinerary legs can be changed."
              : editing
                ? "Change what the reviewing authority asked for, then save and resubmit the mission."
                : "Air and ground operations use separate forms and validations."}
          </p>
        </div>
        <Link
          className="btn btn-ghost btn-md"
          to={editing ? `/missions/${editId}` : `/missions/${form.category}`}
        >
          Cancel
        </Link>
      </div>

      <ErrorBanner message={error} />
      {createdMission && (
        <div className="alert alert-warning">
          <span>Finish the mission record, then add the trip separately.</span>
          <Button variant="ghost" size="sm" onClick={() => navigate(`/missions/${createdMission.id}`)}>
            Open {createdMission.mission_number}
          </Button>
        </div>
      )}

      <form className="mission-form" onSubmit={onSubmit}>
        {!editing && (
        <Card title="Category">
          <Tabs
            tabs={[
              { value: "ground", label: "Ground Operations" },
              { value: "air", label: "Air Operations" },
            ]}
            active={form.category}
            onChange={(value) => setField("category", value)}
          />
          <p className="hint">
            {isGround
              ? "Ground missions require an estimated start and end, the responsible unit and office/section."
              : "Air missions record the flight and the unit it supports; the Operations Center is responsible and no MRM is raised."}
          </p>
        </Card>
        )}

        <Card title="Mission details">
          <div className="form-grid">
            {isGround && (
              <Field label="Mission title" required error={errors.title}>
                <Input
                  value={form.title}
                  onChange={(event) => setField("title", event.target.value)}
                  required
                />
              </Field>
            )}
            {/* Air operations show neither post: the unit is the planner's own
                and the Operations Center is responsible, both applied by the
                server. Ground operations still name them. */}
            {isGround && (
              <>
                <Field
                  label="Unit Primary Responsible (UPR)"
                  required
                  error={errors.unit}
                  hint="Your unit and its subordinate units."
                >
                  <Select
                    value={form.unit}
                    onChange={(event) => changeUnit(event.target.value)}
                    required
                  >
                    <option value="">Select unit…</option>
                    {unitOptions.map((unit) => (
                      <option key={unit.id} value={unit.id}>
                        {unit.acronym || unit.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field
                  label="Office / Section Primary Responsible (O/SPR)"
                  error={errors.office_section}
                  hint={
                    form.unit
                      ? "Offices and sections of the selected unit."
                      : "Select the UPR first; its offices and sections are then listed."
                  }
                >
                  <Select
                    value={form.office_section}
                    onChange={(event) => setField("office_section", event.target.value)}
                    disabled={!form.unit}
                  >
                    <option value="">Not specified</option>
                    {officeOptions.map((office) => (
                      <option key={office.id} value={office.name}>
                        {office.name}
                        {office.code ? ` (${office.code})` : ""}
                      </option>
                    ))}
                  </Select>
                </Field>
              </>
            )}
            {isGround && (
              <Field label="Purpose" className="span-2">
                <Textarea value={form.purpose} onChange={(event) => setField("purpose", event.target.value)} />
              </Field>
            )}
          </div>

          {/* The air form has no mission location: the flight's departure,
              destination and route describe it, so a plotted point would only
              duplicate the itinerary. */}
          {isGround && (
          <div className="form-section">
            <h3>Location</h3>
            <div className="btn-row" style={{ marginBottom: 10 }}>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setLocationTarget({ kind: "mission" })}
              >
                Select on map
              </Button>
              <span className="hint">
                Pick the operational area: the grid reference and location name are derived from the map.
              </span>
            </div>
            <div className="form-grid form-grid-3">
              <Field
                label="Location name"
                hint="Reuse a location already plotted by the Operations Center, or pick a new point on the map."
              >
                <div className="inline-row">
                  <Input
                    value={form.location_name}
                    onChange={(event) => setField("location_name", event.target.value)}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => openOperationalPicker()}
                  >
                    Select from Operational Mapping
                  </Button>
                </div>
              </Field>
              <Field label="Latitude">
                <Input
                  type="number"
                  step="0.000001"
                  value={form.latitude}
                  onChange={(event) => setField("latitude", event.target.value)}
                />
              </Field>
              <Field label="Longitude">
                <Input
                  type="number"
                  step="0.000001"
                  value={form.longitude}
                  onChange={(event) => setField("longitude", event.target.value)}
                />
              </Field>
              <Field
                label="MGRS"
                error={errors.mgrs}
                hint="Enter MGRS, or coordinates, then press Convert to keep both in sync."
              >
                <div className="inline-row">
                  <Input value={form.mgrs} onChange={(event) => setField("mgrs", event.target.value)} />
                  <Button type="button" variant="ghost" size="sm" onClick={resolveMissionCoordinates}>
                    Convert
                  </Button>
                </div>
              </Field>
            </div>

            {aorHandoff && (
              <div className="alert alert-warning" role="status" style={{ marginTop: 12 }}>
                <strong>Outside {unitLabel}'s AOR.</strong> This point falls under{" "}
                <strong>{aorHandoff.unit_acronym}</strong> — {aorHandoff.aor_name}. The
                destination unit's Operations Center is notified and will monitor this
                mission's movement; it can then forward the details to its command posts.
                <label
                  style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 8 }}
                >
                  <input
                    type="checkbox"
                    checked={notifyDestination}
                    onChange={(event) => setNotifyDestination(event.target.checked)}
                  />
                  <span>
                    Notify {aorHandoff.unit_acronym}
                    {aorHandoff.recipient_count
                      ? ` (${aorHandoff.recipient_count} recipient${
                          aorHandoff.recipient_count === 1 ? "" : "s"
                        })`
                      : " — no post holders found; the mission is still created"}
                    .
                  </span>
                </label>
              </div>
            )}
          </div>
          )}

          {isGround ? (
            <div className="form-section">
              <h3>Ground operations</h3>
              <div className="form-grid">
                <Field
                  label="Estimated start"
                  required
                  error={errors["ground_detail"]}
                  hint="Pick the day and time (PST). Past days are unavailable."
                >
                  <DateTimeInput
                    value={form.ground.estimated_start}
                    onChange={(next) => setDetail("ground", "estimated_start", next)}
                  />
                </Field>
                <Field
                  label="Estimated end"
                  required
                  error={endBeforeStart ? "Estimated end must not precede the start." : undefined}
                  hint="Same day: a time earlier than the start is refused."
                >
                  <DateTimeInput
                    value={form.ground.estimated_end}
                    minDateTime={form.ground.estimated_start}
                    onChange={(next) => setDetail("ground", "estimated_end", next)}
                  />
                </Field>
                <Field
                  label="Operational area (primary)"
                  error={errors["ground_detail.operational_area"]}
                  hint="Picked once with the mission location above; there is no second picker for it."
                >
                  <div className="inline-row">
                    <Input
                      value={form.ground.operational_area}
                      readOnly
                      placeholder="Set from the map selection above"
                    />
                    {!form.ground.operational_area && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setLocationTarget({ kind: "mission" })}
                      >
                        Select on map
                      </Button>
                    )}
                  </div>
                </Field>
                <Field label="Ground-specific risk notes" className="span-2">
                  <Textarea
                    value={form.ground.ground_risk_notes}
                    onChange={(event) =>
                      setDetail("ground", "ground_risk_notes", event.target.value)
                    }
                  />
                </Field>
              </div>

              {form.ground.operational_area && (
                <div className="form-section">
                  <h3>Additional operational areas</h3>
                  {form.ground.additional_areas.length === 0 ? (
                    <p className="hint">
                      Not required. Add one only if this mission covers more than the primary
                      area above.
                    </p>
                  ) : (
                    <div className="stop-list">
                      {form.ground.additional_areas.map((row, index) => (
                        <div className="stop-row" key={`area-${index}`}>
                          <span className="stop-index">{index + 2}</span>
                          <Input
                            placeholder="Area name"
                            value={row.name}
                            onChange={(event) =>
                              updateAdditionalArea(index, "name", event.target.value)
                            }
                          />
                          <Input
                            placeholder="MGRS"
                            value={row.mgrs}
                            onChange={(event) =>
                              updateAdditionalArea(index, "mgrs", event.target.value)
                            }
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => setLocationTarget({ kind: "additional", index })}
                          >
                            Map
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => removeAdditionalArea(index)}
                          >
                            ×
                          </Button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="btn-row" style={{ marginTop: 10 }}>
                    <Button type="button" variant="ghost" size="sm" onClick={addAdditionalArea}>
                      Add additional operational area
                    </Button>
                    <span className="hint">
                      The primary area is picked once with the mission location; extras are
                      optional.
                    </span>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <AirFlightOperationsFields
              air={form.air}
              onChange={(nextAir) => setForm((current) => ({ ...current, air: nextAir }))}
              dependsOn={form.depends_on}
              dependencyRelease={form.dependency_release}
              dependencyBuffer={form.dependency_buffer_minutes}
              onDependencyChange={(field, value) =>
                setForm((current) => ({ ...current, [field]: value }))
              }
              dependencyOptions={dependencyOptions}
              dependencyError={dependencyMissions.error}
              onChooseDependency={chooseDependency}
              airAssets={airAssetOptions}
              airAssetConflicts={airConflictRows}
              airConflictsLoading={airConflicts.loading}
              airConflictsError={airConflicts.error}
              detailsLocked={airMissionLocked}
            />
          )}
        </Card>

        {/* Personnel and the MRM belong to ground operations; an air mission
            monitors the flight itself. */}
        {isGround && (
        <Card
          title="Personnel involved"
          actions={
            <Button type="button" variant="ghost" size="sm" onClick={openPersonPicker}>
              Add person
            </Button>
          }
        >
          {personnel.length === 0 && (
            <p className="hint">
              No personnel assigned yet. Use Add person to choose names, then set each one's
              designation here.
            </p>
          )}
          {personnel.length > 0 && (
            <div className="person-list">
              {personnel.map((row, index) => (
                <div
                  className={`person-row${row.role === "Specify" ? " person-row-specify" : ""}`}
                  key={`person-${row.user_id}-${index}`}
                >
                  <span className="person-index">{index + 1}.</span>
                  <span className="person-name" title={personLabel(row.user_id)}>
                    [{personLabel(row.user_id)}]
                  </span>
                  <div className="person-designation">
                    {row.role === "Specify" ? (
                      <>
                        <Input
                          autoFocus
                          aria-label={`Custom designation for ${personLabel(row.user_id)}`}
                          placeholder="Type the exact designation"
                          value={row.custom_role || ""}
                          onChange={(event) => {
                            const next = [...personnel];
                            next[index] = { ...row, custom_role: event.target.value };
                            setPersonnel(next);
                          }}
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          title="Choose a standard designation instead"
                          onClick={() => {
                            const next = [...personnel];
                            next[index] = { ...row, role: "Member", custom_role: "" };
                            setPersonnel(next);
                          }}
                        >
                          Change
                        </Button>
                      </>
                    ) : (
                      <Select
                        aria-label={`Designation for ${personLabel(row.user_id)}`}
                        value={row.role}
                        onChange={(event) => {
                          const next = [...personnel];
                          next[index] = {
                            ...row,
                            role: event.target.value,
                            custom_role:
                              event.target.value === "Specify" ? row.custom_role || "" : "",
                          };
                          setPersonnel(next);
                        }}
                      >
                        {PERSONNEL_ROLES.map((role) => (
                          <option key={role} value={role}>
                            {role}
                          </option>
                        ))}
                      </Select>
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${personLabel(row.user_id)}`}
                    title="Remove from mission"
                    onClick={() => setPersonnel(personnel.filter((_, i) => i !== index))}
                  >
                    ×
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Card>
        )}

        {isGround && (
        <Card title="Mission risk management (MRM)">
          {editing && approvedMrm && !forceMrmUnlink ? (
            <>
              <p className="hint">
                <strong>The MRM for this mission is already approved.</strong>{" "}
                {existing.data?.mrm?.mrm_number ? `${existing.data.mrm.mrm_number}. ` : ""}
                Its hazard scoring, evaluation and sign-off cannot be edited here. Change the
                mission details and save; the approved assessment stays linked.
              </p>
              <label
                className="mrm-unlink"
                style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 4 }}
              >
                <input
                  type="checkbox"
                  checked={forceMrmUnlink}
                  onChange={(event) => setForceMrmUnlink(event.target.checked)}
                />
                <span>
                  Force unlink this approved MRM and create a new one
                  <span className="hint" style={{ display: "block" }}>
                    Saving removes the link. The mission then needs a new assessment before it
                    can be approved or executed.
                  </span>
                </span>
              </label>
            </>
          ) : (
          <>
          {editing && approvedMrm && forceMrmUnlink && (
            <div className="alert alert-warning" role="status" style={{ marginBottom: 10 }}>
              <span>
                Saving unlinks {existing.data?.mrm?.mrm_number || "the approved MRM"}. Start a new
                assessment from the mission afterwards.
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setForceMrmUnlink(false)}
              >
                Keep the approved MRM
              </Button>
            </div>
          )}
          <p className="hint" style={{ marginBottom: 10 }}>
            Select the personnel responsible for this mission's assessment. The creator must be one
            of the personnel involved above; the evaluator must belong to the mission's unit. Both
            are notified immediately and the assessment is linked back to this mission when it is
            started.
          </p>
          <div className="form-grid">
            <Field
              label="MRM creator"
              error={errors.mrm_team}
              hint={
                involvedOptions.length
                  ? "Chosen from the personnel involved in this mission."
                  : "Add the personnel involved first; only they can create the MRM."
              }
            >
              <Select
                value={mrmCreator}
                onChange={(event) => setMrmCreator(event.target.value)}
                disabled={involvedOptions.length === 0}
              >
                <option value="">Not assigned</option>
                {involvedOptions.map((row) => {
                  const person = personOptions.find((item) => item.id === Number(row.user_id));
                  return (
                    <option key={row.user_id} value={row.user_id}>
                      {person ? `${person.full_name} — ${person.serial_number}` : `Personnel #${row.user_id}`}
                      {row.role ? ` · ${personRoleLabel(row)}` : ""}
                    </option>
                  );
                })}
              </Select>
            </Field>
            <Field
              label="MRM evaluator"
              hint={
                form.unit
                  ? "Personnel of the selected unit with MRM evaluation authority."
                  : "Select the UPR first; evaluators are scoped to that unit."
              }
            >
              <Select
                value={mrmEvaluator}
                onChange={(event) => setMrmEvaluator(event.target.value)}
                disabled={!form.unit}
              >
                <option value="">Not assigned</option>
                {evaluatorOptions.map((person) => (
                  <option key={person.id} value={person.id}>
                    {/* full_name already carries the rank: "MSg Joelito S Uriarte" */}
                    {person.full_name} — {person.serial_number}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          {evaluators.error && <p className="field-message">{evaluators.error}</p>}
          </>
          )}
        </Card>
        )}

        {isGround && (
        <Card title="Approving authority">
          {authorityLocked && (
            <p className="hint">
              <strong>Decision authority is fixed.</strong> The approving post, the person and the
              signatory were set when this mission was created and routed, so they cannot be
              changed from the revision form.
            </p>
          )}
          <p className="hint" style={{ marginBottom: 12 }}>
            The Operations Center names the post and the person who will approve this
            mission. The approval task is assigned to that person, so a change of
            incumbent cannot silently move it.
          </p>
          <div className="form-grid">
            <Field
              label="Approving post"
              hint="Only posts with an active holder in this unit are listed below."
            >
              <Select
                value={authorityRole}
                onChange={(event) => {
                  setAuthorityRole(event.target.value);
                  setAuthorityUser("");
                }}
                disabled={authorityLocked}
              >
                <option value="">Select a post</option>
                {postOptions.map((role) => (
                  <option key={role.value} value={role.value}>
                    {role.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Approving authority">
              <Select
                value={authorityUser}
                onChange={(event) => setAuthorityUser(event.target.value)}
                disabled={authorityLocked || !authorityRole}
              >
                <option value="">Select the person</option>
                {authorityOptions.map((person) => (
                  <option key={person.user_id} value={person.user_id}>
                    {person.label}
                  </option>
                ))}
              </Select>
            </Field>
            {isGround && (
              <Field
                label="Mission Order signatory"
                hint="Signs and approves the order after the mission is approved. Defaults to the approving authority if left blank."
              >
                <Select
                  value={orderSignatory}
                  onChange={(event) => setOrderSignatory(event.target.value)}
                  disabled={authorityLocked}
                >
                  <option value="">Default to the approving authority</option>
                  {signatoryOptions.map((person) => (
                    <option key={person.user_id} value={person.user_id}>
                      {person.label}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <Field label="Routing note" className="span-2">
              <Input
                value={authorityNote}
                onChange={(event) => setAuthorityNote(event.target.value)}
                placeholder="Optional note for the approving authority"
                disabled={authorityLocked}
              />
            </Field>
          </div>
          {authorities.error ? (
            <p className="field-message">{authorities.error}</p>
          ) : (
            <>
              {authorityRole && authorityOptions.length === 0 && (
                <p className="field-message">
                  No active person holds this post in the mission's unit. Appoint one before the
                  mission can be routed for approval.
                </p>
              )}
              {!authorityRole && (
                <p className="hint">
                  A draft can be saved without an approving authority, but it cannot be routed
                  for approval or dispatched until one is assigned.
                </p>
              )}
              {routePreview.length > 0 && (
                <div className="alert alert-info" role="status" style={{ marginTop: 12 }}>
                  <strong>Approval route</strong>
                  <p style={{ margin: "4px 0" }}>
                    Operations Center →{" "}
                    {routePreview.map((role) => ROUTE_ROLE_LABELS[role]).join(" → ")}
                  </p>
                  <p className="hint" style={{ margin: 0 }}>
                    Each authority reviews and comments before forwarding.
                    {higherPreview.length > 0
                      ? ` ${higherPreview
                          .map((role) => ROUTE_ROLE_LABELS[role])
                          .join(" and ")} will be notified for visibility but will not approve.`
                      : " No higher authorities are outside this route."}
                  </p>
                </div>
              )}
            </>
          )}
        </Card>
        )}

        {!editing && isGround && (
          <Card
            title="Vehicle trip"
            actions={
              <Button
                type="button"
                variant={tripOpen ? "ghost" : "primary"}
                size="sm"
                onClick={toggleTrip}
              >
                {tripOpen ? "Remove trip" : "Add Vehicle Trip"}
              </Button>
            }
          >
            {!tripOpen ? (
              <p className="hint">
                Optional: attach a vehicle movement with multiple vehicles and an itinerary.
              </p>
            ) : (
              <>
                <div className="form-grid">
                  <Field
                    label="Trip title"
                    hint="Defaults to the mission title; change it if this movement needs its own name."
                  >
                    <Input
                      value={trip.title || form.title}
                      onChange={(event) => setTrip({ ...trip, title: event.target.value })}
                      placeholder={form.title ? `Default: ${form.title}` : "Mission vehicle movement"}
                    />
                  </Field>
                  <Field
                    label="Departure"
                    hint="Defaults to the mission's estimated start. Past days are unavailable."
                  >
                    <DateTimeInput
                      value={trip.departure_at}
                      onChange={(next) => setTrip({ ...trip, departure_at: next })}
                    />
                  </Field>
                  <Field
                    label="Return"
                    error={
                      trip.departure_at &&
                      trip.return_at &&
                      trip.return_at < trip.departure_at
                        ? "Return must not precede departure."
                        : undefined
                    }
                    hint="Defaults to the estimated end. Same day: a time earlier than departure is refused."
                  >
                    <DateTimeInput
                      value={trip.return_at}
                      minDateTime={trip.departure_at}
                      onChange={(next) => setTrip({ ...trip, return_at: next })}
                    />
                  </Field>
                  <Field label="Trip remarks" className="span-2">
                    <Textarea
                      value={trip.remarks}
                      onChange={(event) => setTrip({ ...trip, remarks: event.target.value })}
                    />
                  </Field>
                </div>

                <div className="form-section">
                  <h3>Vehicles and drivers</h3>
                  <p className="hint" style={{ marginTop: 0, marginBottom: 8 }}>
                    A vehicle cannot be added to the trip until its own driver is selected. When the
                    personnel involved include one person designated Driver and the trip uses one
                    vehicle, that driver is selected automatically. With two or more vehicles, set a
                    driver for each; each vehicle needs a different driver.
                  </p>
                  <div className="vehicle-list">
                    {vehicleOptions.length === 0 && <p className="hint">No vehicles registered.</p>}
                    {vehicleOptions.map((vehicle) => {
                      const selected = Boolean(tripPlans[vehicle.id]);
                      // A person removed from the personnel list is no longer a
                      // valid driver, even if their id is still in old state.
                      const driver = tripDrivers[vehicle.id] || "";
                      const hasDriver = driverIsInvolved(vehicle.id);
                      const shownDriver = hasDriver ? driver : "";
                      const autoDriverReady =
                        designatedDriverIds.length === 1 &&
                        Object.keys(tripPlans).length === 0;
                      return (
                        <div className="vehicle-row" key={vehicle.id}>
                          <label
                            className="vehicle-pick"
                            title={
                              hasDriver
                                ? `Add ${vehicleLabel(vehicle)} to this trip`
                                : autoDriverReady
                                  ? `The designated Driver is assigned automatically when this is the only vehicle`
                                  : "Choose a driver before adding this vehicle"
                            }
                          >
                            <input
                              type="checkbox"
                              checked={selected}
                              disabled={!hasDriver && !selected && !autoDriverReady}
                              onChange={() => toggleVehicle(vehicle.id)}
                            />
                            <span>
                              <span className="vehicle-name">{vehicleLabel(vehicle)}</span>
                              <span className="vehicle-note">
                                {vehicle.wheels ? `${vehicle.wheels}-wheel` : ""}
                                {vehicle.capacity ? ` · ${vehicle.capacity} capacity` : ""}
                              </span>
                            </span>
                          </label>
                          <SearchableSelect
                            value={shownDriver}
                            onChange={(next) => chooseDriver(vehicle.id, next)}
                            options={driverChoicesFor(vehicle.id)}
                            placeholder="Search driver by name or serial…"
                            ariaLabel={`Driver for ${vehicleLabel(vehicle)}`}
                            emptyMessage={
                              driverChoices.length
                                ? "Every involved person is already driving another vehicle."
                                : "Add the personnel involved first; only they can be designated."
                            }
                          />
                          <span className={`vehicle-state ${selected && hasDriver ? "ready" : ""}`}>
                            {selected
                              ? hasDriver
                                ? "Added to trip"
                                : "Choose a driver again"
                              : hasDriver
                                ? "Ready — tick to add"
                                : autoDriverReady
                                  ? "Driver auto-assigned — tick to add"
                                  : "Choose a driver first"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  <div className="btn-row" style={{ marginTop: 12 }}>
                    <Button type="button" variant="ghost" size="sm" onClick={addPov}>
                      Add POV
                    </Button>
                    <span className="hint">
                      A POV is a vehicle possessed by one of the personnel on this mission. The
                      Operations Center describes it here; it is not added to the unit vehicle
                      register.
                    </span>
                  </div>
                  {povs.map((row) => {
                    const key = `${POV_PREFIX}${row.ref}`;
                    const position = povs.indexOf(row) + 1;
                    return (
                      <div className="pov-card" key={key}>
                        <div className="pov-head">
                          <strong>Privately owned vehicle {position}</strong>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => removePov(row.ref)}
                          >
                            Remove
                          </Button>
                        </div>
                        <div className="form-grid">
                          <Field label="Vehicle type" required>
                            <Select
                              value={row.vehicle_type}
                              onChange={(event) =>
                                updatePov(row.ref, "vehicle_type", event.target.value)
                              }
                              required
                            >
                              <option value="">Choose vehicle type…</option>
                              {POV_TYPES.map((type) => (
                                <option key={type} value={type}>
                                  {type}
                                </option>
                              ))}
                            </Select>
                          </Field>
                          <Field label="Model" required>
                            <Input
                              value={row.make_model}
                              onChange={(event) =>
                                updatePov(row.ref, "make_model", event.target.value)
                              }
                              placeholder="Honda XR150, Toyota Hilux…"
                            />
                          </Field>
                          <Field label="Plate number" required>
                            <Input
                              value={row.plate_number}
                              onChange={(event) =>
                                updatePov(row.ref, "plate_number", event.target.value)
                              }
                              placeholder="e.g. PN-12315"
                              required
                            />
                          </Field>
                          <Field label="Gas / fuel" required>
                            <select
                              className="input"
                              value={row.fuel_type}
                              onChange={(event) =>
                                updatePov(row.ref, "fuel_type", event.target.value)
                              }
                            >
                              <option value="">Choose fuel…</option>
                              {POV_FUELS.map((fuel) => (
                                <option key={fuel} value={fuel}>
                                  {fuel}
                                </option>
                              ))}
                            </select>
                          </Field>
                          <Field
                            label="Owner"
                            required
                            hint="Searched among this mission's personnel."
                          >
                            <SearchableSelect
                              value={row.owner}
                              onChange={(next) => updatePov(row.ref, "owner", next)}
                              options={driverChoices}
                              placeholder="Search owner by name or serial…"
                              ariaLabel={`Owner of POV ${position}`}
                              emptyMessage="Add the personnel involved first; the owner must be on this mission."
                            />
                          </Field>
                          <Field label="Driver" hint="Defaults to the owner.">
                            <SearchableSelect
                              value={row.driver}
                              onChange={(next) => updatePov(row.ref, "driver", next)}
                              options={driverChoicesFor(key)}
                              placeholder="Search driver by name or serial…"
                              ariaLabel={`Driver for POV ${position}`}
                              emptyMessage="Every involved person is already driving another vehicle."
                            />
                          </Field>
                        </div>
                        <p className="hint" style={{ margin: 0 }}>
                          Added to the trip. Plan its itinerary below like any other vehicle.
                        </p>
                      </div>
                    );
                  })}
                  {errors.vehicles && <p className="field-message">{errors.vehicles}</p>}
                  <p className="hint" style={{ marginTop: 8 }}>
                    Each vehicle keeps its own itinerary and its own road route. Vehicles come from
                    the unit inventory, or are a POV described here.
                  </p>
                </div>

                <div className="form-section">
                  <h3>Itinerary by vehicle</h3>
                  {Object.keys(tripPlans).length === 0 ? (
                    <p className="hint">Select a vehicle above to plan its route.</p>
                  ) : (
                  <>
                  <div className="btn-row" style={{ marginBottom: 10 }}>
                    {Object.keys(tripPlans).map((planKey) => (
                      <Button
                        key={planKey}
                        type="button"
                        variant={String(activeVehicle) === planKey ? "primary" : "ghost"}
                        size="sm"
                        onClick={() => setActiveVehicle(planKey)}
                      >
                        {planLabel(planKey)}
                      </Button>
                    ))}
                  </div>
                  <div className="stop-list">
                    {(tripPlans[activeVehicle] || []).map((stop, index) => (
                      <div
                        className={`itinerary-stop${draggedStop === index ? " is-dragging" : ""}${
                          dragOverStop === index && draggedStop !== index ? " is-drop-target" : ""
                        }`}
                        key={`stop-${index}`}
                        onDragOver={(event) => {
                          if (draggedStop === null || draggedStop === index) return;
                          event.preventDefault();
                          event.dataTransfer.dropEffect = "move";
                          if (dragOverStop !== index) setDragOverStop(index);
                        }}
                        onDragLeave={(event) => {
                          if (!event.currentTarget.contains(event.relatedTarget)) {
                            setDragOverStop(null);
                          }
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          const from =
                            draggedStop !== null
                              ? draggedStop
                              : Number(event.dataTransfer.getData("text/plain"));
                          if (Number.isInteger(from)) moveStop(from, index);
                          setDraggedStop(null);
                          setDragOverStop(null);
                        }}
                      >
                        <span
                          className="stop-drag"
                          draggable
                          aria-label={`Drag stop ${index + 1} to reorder`}
                          title="Drag to reorder this stop"
                          onDragStart={(event) => {
                            event.dataTransfer.effectAllowed = "move";
                            event.dataTransfer.setData("text/plain", String(index));
                            const row = event.currentTarget.closest(".itinerary-stop");
                            if (row) event.dataTransfer.setDragImage(row, 12, 12);
                            setDraggedStop(index);
                          }}
                          onDragEnd={() => {
                            setDraggedStop(null);
                            setDragOverStop(null);
                          }}
                        >
                          ⋮⋮
                        </span>
                        <span className="stop-index">{index + 1}</span>
                        <Input
                          placeholder="Location"
                          value={stop.location_name}
                          onChange={(event) => updateStop(index, "location_name", event.target.value)}
                        />
                        <Input
                          placeholder="MGRS"
                          value={stop.mgrs}
                          onChange={(event) => updateStop(index, "mgrs", event.target.value)}
                        />
                        <Input
                          placeholder="Remarks"
                          value={itineraryRemark(index, tripPlans[activeVehicle] || [])}
                          readOnly
                          aria-label={`Remarks for stop ${index + 1}`}
                          title="Set automatically from the stop order"
                        />
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Pin stop ${index + 1} on the map`}
                          onClick={() =>
                            setLocationTarget({
                              kind: "stop",
                              vehicleId: activeVehicle,
                              index,
                            })
                          }
                          title="Search the map and pin this stop to get its exact MGRS"
                        >
                          <StopActionIcon name="map" />
                          <span className="stop-action-label">Map</span>
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Choose stop ${index + 1} from Operational Mapping`}
                          onClick={() =>
                            openOperationalPicker({
                              kind: "stop",
                              vehicleId: activeVehicle,
                              index,
                            })
                          }
                          title="Reuse a saved operational-mapping location"
                        >
                          <StopActionIcon name="operational" />
                          <span className="stop-action-label">Operational Map</span>
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Remove stop ${index + 1}`}
                          onClick={() => removeStop(index)}
                          title="Remove this stop"
                        >
                          <StopActionIcon name="remove" />
                          <span className="stop-action-label">Remove</span>
                        </Button>
                      </div>
                    ))}
                  </div>
                  <div className="btn-row" style={{ marginTop: 10 }}>
                    <Button type="button" variant="ghost" size="sm" onClick={addStop}>
                      Add stop
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={addReturnToOrigin}>
                      Return to origin
                    </Button>
                    <span className="hint">
                      Stop 1 defaults to the vehicle's unit location and the final stop to the
                      mission Location; Add stop inserts between them, and Return to origin closes
                      the loop. Drag the handle at the left of a stop to reorder the itinerary. Use
                      Map for a new point or Operational Map to reuse a saved location. The driving
                      route is calculated from the road network after saving — never as a straight
                      line.
                    </span>
                  </div>
                  </>
                  )}
                </div>
              </>
            )}
          </Card>
        )}

        <div className="btn-row">
          <Button
            type="submit"
            disabled={busy}
            onClick={() => {
              submitIntent.current = "save";
            }}
          >
            {busy
              ? "Saving…"
              : airMissionLocked
                ? "Save itinerary changes"
                : editing
                  ? "Update mission"
                  : "Create mission"}
          </Button>
          {editing &&
            !airMissionLocked &&
            existing.data?.approval?.status === "returned" && (
            <Button
              type="submit"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                submitIntent.current = "resubmit";
              }}
            >
              {busy ? "Saving…" : "Update and resubmit for approval"}
            </Button>
          )}
          <Link
            className="btn btn-ghost btn-md"
            to={editing ? `/missions/${editId}` : `/missions/${form.category}`}
          >
            Cancel
          </Link>
        </div>
      </form>

      {personPickerOpen && (
        <Modal
          title="Add personnel"
          onClose={() => setPersonPickerOpen(false)}
          footer={
            <>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setPersonPickerOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                disabled={personPicks.length === 0}
                onClick={addPickedPeople}
              >
                {personPicks.length
                  ? `Add selected (${personPicks.length})`
                  : "Add selected"}
              </Button>
            </>
          }
        >
          <p className="hint" style={{ marginBottom: 10 }}>
            Select one or more people, then set each one's designation in the list after the
            dialog closes.
          </p>
          <Input
            value={personQuery}
            onChange={(event) => setPersonQuery(event.target.value)}
            placeholder="Search rank, name or serial number…"
            aria-label="Search personnel"
            autoFocus
          />
          <div className="person-picker-list">
            {filteredPickerOptions.map((person) => {
              const checked = personPicks.includes(person.id);
              return (
                <label
                  key={person.id}
                  className={`person-pick ${checked ? "checked" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => togglePersonPick(person.id)}
                  />
                  <span className="person-pick-name">
                    {person.full_name}-{person.serial_number}
                  </span>
                </label>
              );
            })}
            {filteredPickerOptions.length === 0 && (
              <p className="searchable-empty">
                {pickerOptions.length
                  ? "No personnel match that search."
                  : "Everyone in the roster is already on this mission."}
              </p>
            )}
          </div>
        </Modal>
      )}

      {operationalPickerOpen && (
        <OperationalLocationPickerModal
          onClose={() => {
            setOperationalPickerOpen(false);
            setOperationalTarget(null);
          }}
          onApply={(picked) => {
            applyLocation(picked, operationalTarget);
            setOperationalPickerOpen(false);
            setOperationalTarget(null);
          }}
        />
      )}

      {destinationPickerOpen && (
        <OperationalLocationPickerModal
          title="Select air mission destination"
          allowManual
          includeUnits
          includeAirfields
          featureTypes={["friendly_troop"]}
          onClose={() => setDestinationPickerOpen(false)}
          onApply={(picked) => {
            setDetail(
              "air",
              "destination_location",
              operationalLocationLine(picked)
            );
            setDestinationPickerOpen(false);
          }}
        />
      )}

      {locationTarget && (
        <LocationPickerModal
          title={
            locationTarget.kind === "stop"
              ? `Pin itinerary stop ${locationTarget.index + 1}`
              : undefined
          }
          initial={
            locationTarget.kind === "stop"
              ? {
                  latitude:
                    (tripPlans[locationTarget.vehicleId] || [])[locationTarget.index]
                      ?.latitude || "",
                  longitude:
                    (tripPlans[locationTarget.vehicleId] || [])[locationTarget.index]
                      ?.longitude || "",
                  mgrs:
                    (tripPlans[locationTarget.vehicleId] || [])[locationTarget.index]?.mgrs ||
                    "",
                  location_name:
                    (tripPlans[locationTarget.vehicleId] || [])[locationTarget.index]
                      ?.location_name || "",
                }
              : locationTarget.kind === "additional"
              ? {
                  latitude: form.ground.additional_areas[locationTarget.index]?.latitude || "",
                  longitude: form.ground.additional_areas[locationTarget.index]?.longitude || "",
                  mgrs: form.ground.additional_areas[locationTarget.index]?.mgrs || "",
                  location_name: form.ground.additional_areas[locationTarget.index]?.name || "",
                }
              : {
                  latitude: form.latitude,
                  longitude: form.longitude,
                  mgrs: form.mgrs,
                  location_name: form.location_name,
                }
          }
          unitId={form.unit}
          onClose={() => setLocationTarget(null)}
          onApply={applyLocation}
        />
      )}
    </>
  );
}
