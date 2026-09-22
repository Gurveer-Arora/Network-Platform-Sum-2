const PER_PAGE = 15;

const grid = document.querySelector("#fleet");
const count = document.querySelector("#fleet-count");
const pager = document.querySelector("#fleet-pagination");
const filters = document.querySelector("#filters");

const PAINT = {
    aquamarine: "#7FCFC4", blue: "#2C4A7C", crimson: "#8E1F2F", fuscia: "#B3488E",
    goldenrod: "#C9A227", green: "#3F5D52", indigo: "#3B3F7A", khaki: "#A89A6B",
    maroon: "#6B2233", mauv: "#9B7CA8", orange: "#C56A2C", pink: "#C27B94",
    puce: "#8E5A66", purple: "#5B3A7C", red: "#9B2C2C", teal: "#2F6B6B",
    turquoise: "#3FA39B", violet: "#7C5AA0", yellow: "#D9A521",
};

let vehicles = [];

// --------------------------------------------------------------------------
// Cards
// --------------------------------------------------------------------------
function buildCard(vehicle) {
    const card = document.createElement("li");
    card.className = "vehicle-card";

    card.innerHTML = `
      <div class="vehicle-card__ribbon">
        <span class="vehicle-card__category">${vehicle.category}</span>
        <span class="vehicle-card__status is-${vehicle.status.toLowerCase()}">${vehicle.status}</span>
      </div>

      <h3 class="vehicle-card__name">${vehicle.make} ${vehicle.model}</h3>

      <p class="vehicle-card__identity">
        <span class="vehicle-card__paint" style="--paint: ${PAINT[vehicle.colour.toLowerCase()]}"></span>
        ${vehicle.colour}
        <span class="vehicle-card__year">${vehicle.year}</span>
      </p>

      <p class="vehicle-card__rate">
        <span class="vehicle-card__rate-value">£${vehicle.dayRate}</span>
        <span class="vehicle-card__rate-unit">per day</span>
      </p>

      <dl class="vehicle-card__plate">
        <div><dt>Seats</dt><dd>${vehicle.numberSeats}</dd></div>
        <div><dt>Economy</dt><dd>${vehicle.fuelEconomy} mpg</dd></div>
        <div class="vehicle-card__plate-wide"><dt>Collect from</dt><dd>${vehicle.branch}</dd></div>
      </dl>
    `;
    return card;
}

// --------------------------------------------------------------------------
// Filters — these DO hit the API
// --------------------------------------------------------------------------
function applyFilters(event) {
    event.preventDefault();

    const params = new URLSearchParams();
    for (const [name, value] of new FormData(filters)) {
        if (value) params.append(name, value);
    }

    history.pushState({}, "", `${location.pathname}?${params}`);
    loadFleet();
}

function fillFilters() {
    filters.reset();
    const params = new URLSearchParams(location.search);

    for (const field of filters.elements) {
        if (field.type === "checkbox") {
            field.checked = params.getAll(field.name).includes(field.value);
        } else if (params.has(field.name)) {
            field.value = params.get(field.name);
        }
    }
}

function clearFilters() {
    history.pushState({}, "", location.pathname);
    fillFilters();
    loadFleet();
}

// --------------------------------------------------------------------------
// Pagination — these do NOT hit the API
// --------------------------------------------------------------------------
function pageButton(label, target, current) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "fleet-page";
    button.textContent = label;

    if (current) {
        button.className = "fleet-page is-current";
        button.disabled = true;
    } else {
        button.addEventListener("click", () => goToPage(target));
    }

    return button;
}

function renderPager(page, totalPages) {
    pager.innerHTML = "";

    if (page > 1) pager.append(pageButton("Previous", page - 1, false));

    for (let p = page - 2; p <= page + 2; p++) {
        if (p >= 1 && p <= totalPages) pager.append(pageButton(p, p, p === page));
    }

    if (page < totalPages) pager.append(pageButton("Next", page + 1, false));
}

function currentPage() {
    return Number(new URLSearchParams(location.search).get("page")) || 1;
}

function goToPage(page) {
    const params = new URLSearchParams(location.search);
    params.set("page", page);
    history.pushState({}, "", `${location.pathname}?${params}`);

    showPage();
    document.querySelector(".section-head").scrollIntoView({ block: "start" });
}

function showPage() {
    const page = currentPage();
    const start = (page - 1) * PER_PAGE;
    const slice = vehicles.slice(start, start + PER_PAGE);

    const list = document.createElement("ul");
    list.className = "fleet-list";
    slice.forEach((vehicle) => list.append(buildCard(vehicle)));

    grid.replaceChildren(list);
    count.textContent = `Showing ${start + 1}–${start + slice.length} of ${vehicles.length}`;
    renderPager(page, Math.ceil(vehicles.length / PER_PAGE));
}

// --------------------------------------------------------------------------
// Fetch. Every filter goes to the API, the page number never does.
// --------------------------------------------------------------------------
async function loadFleet() {
    const params = new URLSearchParams(location.search);
    params.delete("page");

    const query = params.toString();
    const response = await fetch(query ? `${grid.dataset.endpoint}?${query}` : grid.dataset.endpoint);

    vehicles = await response.json();
    showPage();
}

filters.addEventListener("change", applyFilters);
filters.addEventListener("submit", applyFilters);
document.querySelector("#filters-clear").addEventListener("click", clearFilters);

window.addEventListener("popstate", () => {
    fillFilters();
    loadFleet();
});

fillFilters();
loadFleet();