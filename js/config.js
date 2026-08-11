/* ============================================================
   Groundwork ATX — configuration
   Datasets are Socrata (SODA) resources on data.austintexas.gov.
   Field names are *candidates*: the data layer resolves the real
   field names at runtime from dataset metadata, so schema drift
   on the city's side degrades gracefully instead of breaking.
   ============================================================ */

const GW_CONFIG = {
  portal: "https://data.austintexas.gov",

  // Optional Socrata app token (raises rate limits; not required).
  appToken: null,

  cacheTTLminutes: 10,

  map: {
    center: [30.2695, -97.7426], // Austin — Congress & the river
    zoom: 12,
    tiles: {
      url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    },
  },

  datasets: {
    permits: {
      id: "3syk-w9eu",
      name: "Issued Construction Permits",
      page: "https://data.austintexas.gov/Building-and-Development/Issued-Construction-Permits/3syk-w9eu",
      fields: {
        date:       ["issued_date", "issue_date", "applieddate", "applied_date"],
        applied:    ["applieddate", "applied_date"],
        type:       ["permittype", "permit_type"],
        typeDesc:   ["permit_type_desc", "permit_type_description", "permittype_desc"],
        workClass:  ["work_class", "workclass"],
        classMapped:["permit_class_mapped", "permit_class"],
        classDetail:["permit_class"],
        number:     ["permit_number", "permit_num", "permitnum"],
        status:     ["status_current", "current_status", "status"],
        desc:       ["description", "project_description"],
        address:    ["original_address1", "project_address", "address"],
        zip:        ["original_zip", "zip"],
        district:   ["council_district", "district"],
        lat:        ["latitude", "lat"],
        lon:        ["longitude", "lon", "lng"],
        valuation:  ["total_job_valuation", "total_valuation", "job_valuation", "declared_valuation"],
        link:       ["link", "url", "permit_link"],
        applicant:  ["applicant_org", "applicant_full_name", "applicant"],
        contractor: ["contractor_company_name", "contractor_full_name"],
        sqftNew:    ["total_new_add_sqft", "new_add_sqft"],
        sqftRemodel:["remodel_repair_sqft"],
      },
    },

    zoning: {
      id: "edir-dcnf",
      name: "Zoning Cases",
      page: "https://data.austintexas.gov/Building-and-Development/Zoning-Cases/edir-dcnf",
      fields: {
        date:    ["filed_date", "application_date", "created_date", "case_filed_date", "last_update", "status_date"],
        number:  ["case_number", "zoning_case_number", "case_num", "case"],
        name:    ["case_name", "project_name", "name"],
        desc:    ["request", "description", "proposed_zoning", "case_description"],
        status:  ["status", "case_status", "status_description"],
        manager: ["case_manager", "planner"],
        address: ["address", "project_address", "location_address", "case_address"],
        district:["council_district", "district"],
        lat:     ["latitude", "lat"],
        lon:     ["longitude", "lon", "lng"],
        fromZone:["existing_zoning", "current_zoning", "zoning_from"],
        toZone:  ["proposed_zoning", "requested_zoning", "zoning_to"],
        link:    ["link", "url", "case_link"],
      },
    },

    co: {
      id: "f9mz-m6dy",
      name: "Certificates of Occupancy",
      page: "https://data.austintexas.gov/Building-and-Development/Certificates-Of-Occupancy/f9mz-m6dy",
      fields: {
        date:    ["issued_date", "issue_date", "co_issue_date", "date_issued", "applieddate"],
        number:  ["permit_number", "co_number", "certificate_number", "permit_num"],
        type:    ["permit_type_desc", "permit_type", "co_type", "occupancy_type"],
        use:     ["existing_use", "proposed_use", "use_category", "primary_use", "description"],
        desc:    ["description", "project_name", "business_name"],
        status:  ["status_current", "status"],
        address: ["original_address1", "address", "project_address"],
        district:["council_district", "district"],
        lat:     ["latitude", "lat"],
        lon:     ["longitude", "lon", "lng"],
        link:    ["link", "url"],
      },
    },

    council: {
      id: "sich-49ay",
      name: "Council Agenda Items (Feb 2024–present)",
      page: "https://data.austintexas.gov/City-Government/City-of-Austin-Council-Agenda-Items-Updates-Februa/sich-49ay",
      fields: {
        date:   ["meeting_date", "council_meeting_date", "agenda_date", "date"],
        title:  ["title", "agenda_item", "item_text", "caption", "recommendation", "agenda_item_description", "description"],
        item:   ["item_number", "agenda_item_number", "item"],
        dept:   ["department", "dept", "sponsoring_department"],
        status: ["status", "item_status", "action"],
        district:["district", "districts_impacted", "council_district"],
        caseNo: ["zoning_case_number", "case_number"],
        link:   ["link", "url", "backup_link"],
      },
    },
  },

  /* CRE legislation lens — keyword groups used to flag council agenda
     items that matter to commercial real estate. Matching is
     case-insensitive, client-side, against the item's full text. */
  creKeywords: [
    { key: "zoning",      label: "Zoning & rezoning",   terms: ["rezon", "zoning", "c14-", "npa-", "neighborhood plan amendment"] },
    { key: "ldc",         label: "Land Dev. Code",      terms: ["land development code", "ldc", "code amendment", "title 25"] },
    { key: "siteplan",    label: "Site plans & PUDs",   terms: ["site plan", "planned unit development", "pud", "subdivision", "plat"] },
    { key: "density",     label: "Density & bonus",     terms: ["density bonus", "db90", "affordability unlocked", "vmu", "vertical mixed use", "compatibility"] },
    { key: "transit",     label: "Transit & ETOD",      terms: ["etod", "transit-oriented", "transit oriented", "project connect", "light rail"] },
    { key: "permitting",  label: "Permitting process",  terms: ["permit", "development services", "site plan review", "inspection", "certificate of occupancy"] },
    { key: "fees",        label: "Fees & taxes",        terms: ["impact fee", "fee schedule", "fee-in-lieu", "tax increment", "tirz", "abatement"] },
    { key: "historic",    label: "Historic & design",   terms: ["historic zoning", "historic landmark", "demolition", "design standards", "signage", "sign regulation"] },
    { key: "downtown",    label: "Downtown & districts",terms: ["downtown", "south central waterfront", "domain", "mueller", "seaholm", "rainey"] },
    { key: "parking",     label: "Parking & mobility",  terms: ["parking requirement", "parking minimum", "right-of-way", "sidewalk", "driveway"] },
  ],

  /* External calendars & references shown on the Council Watch tab —
     the debate itself happens in these rooms. */
  councilLinks: [
    { name: "Council Meeting Information Center", url: "https://www.austintexas.gov/department/city-council/council/council_meeting_info_center.htm", note: "Official agendas, backup documents, and meeting schedule" },
    { name: "City of Austin public calendar", url: "https://www.austintexas.gov/calendar", note: "All public meetings, hearings, and events" },
    { name: "Planning Commission", url: "https://www.austintexas.gov/planningcommission", note: "Zoning cases and code amendments are heard here before Council" },
    { name: "Zoning & Platting Commission", url: "https://www.austintexas.gov/zapcommission", note: "Zoning and platting cases outside neighborhood-plan areas" },
    { name: "Board of Adjustment", url: "https://www.austintexas.gov/boaboard", note: "Variances and special exceptions" },
    { name: "Historic Landmark Commission", url: "https://www.austintexas.gov/hlc", note: "Demolition, relocation, and historic-zoning cases" },
    { name: "Design Commission", url: "https://www.austintexas.gov/designcommission", note: "Downtown density bonus & design standards review" },
    { name: "AB+C public permit search", url: "https://abc.austintexas.gov/", note: "Look up any permit, folder, or case by address or number" },
    { name: "Development Services Department", url: "https://www.austintexas.gov/department/development-services", note: "Permitting, site plan review, trade permits, C of O" },
  ],
};
