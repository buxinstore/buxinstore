/**
 * Shipping Selector Component
 * Handles shipping mode selection and price calculation
 */

class ShippingSelector {
    constructor(options = {}) {
        this.container = options.container || document.getElementById('shipping-selector');
        this.countryInput = options.countryInput || document.getElementById('shipping-country');
        this.weightInput = options.weightInput || document.getElementById('shipping-weight');
        this.modeContainer = options.modeContainer || document.getElementById('shipping-modes');
        this.priceDisplay = options.priceDisplay || document.getElementById('shipping-price');
        this.onSelect = options.onSelect || null;
        
        this.selectedMode = null;
        this.selectedCountry = null;
        this.totalWeight = 0;
        this.modes = [];
        this.prices = {};
        
        this.init();
    }
    
    async init() {
        await this.loadModes();
        this.setupEventListeners();
        this.render();
    }
    
    async loadModes() {
        try {
            const response = await fetch('/api/shipping/modes');
            const data = await response.json();
            this.modes = data;
        } catch (error) {
            console.error('Error loading shipping modes:', error);
        }
    }
    
    setupEventListeners() {
        if (this.countryInput) {
            this.countryInput.addEventListener('change', (e) => {
                this.selectedCountry = e.target.value;
                this.calculatePrices();
            });
        }
        
        if (this.weightInput) {
            this.weightInput.addEventListener('input', (e) => {
                this.totalWeight = parseFloat(e.target.value) || 0;
                this.calculatePrices();
            });
        }
    }
    
    async calculatePrices() {
        if (!this.selectedCountry || !this.totalWeight) {
            this.prices = {};
            this.render();
            return;
        }
        
        // Calculate price for each mode
        for (const mode of this.modes) {
            try {
                const response = await fetch('/api/shipping/calculate', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        country: this.selectedCountry,
                        shipping_mode: mode.key,
                        total_weight: this.totalWeight
                    })
                });
                
                const data = await response.json();
                if (data.available && !data.error) {
                    this.prices[mode.key] = {
                        price: data.shipping_fee_gmd,
                        display: data.shipping_fee_display,
                        delivery_time: data.delivery_time,
                        rule_id: data.rule_id
                    };
                } else {
                    this.prices[mode.key] = {
                        available: false,
                        error: data.error || 'Not available'
                    };
                }
            } catch (error) {
                console.error(`Error calculating price for ${mode.key}:`, error);
                this.prices[mode.key] = {
                    available: false,
                    error: 'Calculation failed'
                };
            }
        }
        
        this.render();
    }
    
    selectMode(modeKey) {
        this.selectedMode = modeKey;
        this.render();
        
        if (this.onSelect) {
            const mode = this.modes.find(m => m.key === modeKey);
            const price = this.prices[modeKey];
            this.onSelect({
                mode: mode,
                price: price,
                country: this.selectedCountry,
                weight: this.totalWeight
            });
        }
    }
    
    render() {
        if (!this.modeContainer) return;
        
        if (!this.selectedCountry) {
            this.modeContainer.innerHTML = `
                <div class="p-4 text-center text-gray-500 dark:text-gray-400">
                    <p>Select country to view shipping options</p>
                </div>
            `;
            return;
        }
        
        if (!this.totalWeight || this.totalWeight <= 0) {
            this.modeContainer.innerHTML = `
                <div class="p-4 text-center text-gray-500 dark:text-gray-400">
                    <p>Enter weight to calculate shipping</p>
                </div>
            `;
            return;
        }
        
        const modesHtml = this.modes.map(mode => {
            const price = this.prices[mode.key];
            const isSelected = this.selectedMode === mode.key;
            const isAvailable = price && price.available !== false;
            
            return `
                <div class="border rounded-lg p-4 cursor-pointer transition-all ${
                    isSelected 
                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' 
                        : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
                } ${!isAvailable ? 'opacity-50' : ''}" 
                     onclick="shippingSelector.selectMode('${mode.key}')">
                    <div class="flex items-start justify-between">
                        <div class="flex-1">
                            <div class="flex items-center gap-2 mb-2">
                                <span class="text-2xl">${mode.icon || '📦'}</span>
                                <h3 class="font-semibold text-gray-900 dark:text-white">${mode.label}</h3>
                                ${isSelected ? '<span class="text-blue-500">✓</span>' : ''}
                            </div>
                            <p class="text-sm text-gray-600 dark:text-gray-400 mb-2">${mode.description || ''}</p>
                            <div class="flex items-center gap-4 text-sm">
                                <span class="text-gray-500 dark:text-gray-400">
                                    <i class="fas fa-clock mr-1"></i>
                                    ${mode.delivery_time_range || 'N/A'}
                                </span>
                            </div>
                        </div>
                        <div class="text-right ml-4">
                            ${isAvailable ? `
                                <div class="text-2xl font-bold text-gray-900 dark:text-white">
                                    ${price.display || `D${price.price.toFixed(2)}`}
                                </div>
                                ${price.delivery_time ? `
                                    <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">
                                        ${price.delivery_time}
                                    </div>
                                ` : ''}
                            ` : `
                                <div class="text-sm text-red-500 dark:text-red-400">
                                    ${price?.error || 'Not available'}
                                </div>
                            `}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
        
        this.modeContainer.innerHTML = modesHtml;
        
        // Update price display
        if (this.priceDisplay && this.selectedMode) {
            const price = this.prices[this.selectedMode];
            if (price && price.available !== false) {
                this.priceDisplay.textContent = price.display || `D${price.price.toFixed(2)}`;
            } else {
                this.priceDisplay.textContent = 'Not available';
            }
        }
    }
    
    getSelectedShipping() {
        if (!this.selectedMode) return null;
        
        const mode = this.modes.find(m => m.key === this.selectedMode);
        const price = this.prices[this.selectedMode];
        
        return {
            mode_key: this.selectedMode,
            mode_label: mode?.label,
            price_gmd: price?.price,
            delivery_time: price?.delivery_time,
            rule_id: price?.rule_id,
            country: this.selectedCountry,
            weight: this.totalWeight
        };
    }
}

// Global instance (can be overridden)
let shippingSelector = null;

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('shipping-selector')) {
            shippingSelector = new ShippingSelector();
        }
    });
} else {
    if (document.getElementById('shipping-selector')) {
        shippingSelector = new ShippingSelector();
    }
}

