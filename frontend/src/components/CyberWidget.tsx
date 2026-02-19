import type { ReactNode } from "react";

interface CyberWidgetProps {
    children: ReactNode;
    colorTheme: "orange" | "green" | "red" | "grey" | "dark";
    headerTitle?: string;
    headerCode?: string;
    footerCode?: string;
    className?: string;
}

const themeClasses = {
    orange: "bg-(--color-cyber-orange) text-black border-(--color-cyber-orange)",
    green: "bg-(--color-cyber-green) text-black border-(--color-cyber-green)",
    red: "bg-(--color-cyber-red) text-black border-(--color-cyber-red)",
    grey: "bg-(--color-cyber-grey) text-black border-(--color-cyber-grey)",
    dark: "bg-[#2C2C2C] text-white border-gray-600",
};

export function CyberWidget({ children, colorTheme, headerTitle, headerCode, footerCode, className = "" }: CyberWidgetProps) {
    const currentTheme = themeClasses[colorTheme] || themeClasses.dark;

    return (
        <div
            className={`relative overflow-hidden rounded-[24px] border-4 p-6 shadow-xl ${currentTheme} ${className}`}
        >
            {/* Noise overlay */}
            <div className="bg-noise absolute inset-0 pointer-events-none opacity-20 mix-blend-multiply rounded-[20px]" />

            {/* Top Header Section */}
            {(headerTitle || headerCode) && (
                <div className="mb-4 flex items-center justify-between border-b-2 border-black/20 pb-2 relative z-10">
                    <div className="font-tech text-3xl font-bold uppercase tracking-widest flex items-center gap-2">
                        {headerTitle}
                        {colorTheme === 'orange' && <span className="w-2 h-2 rounded-full bg-orange-700/50 block ml-1" />}
                    </div>
                    {headerCode && (
                        <div className="font-mono text-xs uppercase border border-black/20 px-2 py-0.5 rounded-sm">
                            {headerCode}
                        </div>
                    )}
                </div>
            )}

            {/* Main Content */}
            <div className="relative z-10 font-sans flex-1 min-h-0 flex flex-col">
                {children}
            </div>

            {/* Footer Section */}
            {footerCode && (
                <div className="mt-6 flex items-center justify-between border-t-2 border-black/20 pt-3 relative z-10">
                    <div className="font-tech font-bold text-lg">{footerCode}</div>
                    <div className="flex items-center gap-2">
                        <BarCode mini />
                        <div className="font-tech text-xs tracking-widest italic opacity-70 flex items-center gap-2">
                            CORP. INC <span className="text-[8px]">&copy;</span>
                            <div className="flex gap-1 ml-2">
                                <div className="w-3 h-3 rounded-full border border-black overflow-hidden relative">
                                    <div className="absolute inset-0 bg-transparent border-[4px] border-transparent border-t-black border-r-black rotate-45 transform"></div>
                                </div>
                                <div className="w-3 h-3 rounded-full bg-black"></div>
                                <div className="w-3 h-3 rounded-full border border-black overflow-hidden relative">
                                    <div className="absolute inset-0 bg-transparent border-[4px] border-transparent border-b-black rotate-12 transform"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export function BarCode({ mini = false }: { mini?: boolean }) {
    return (
        <div className={`flex items-end ${mini ? "h-4 gap-[1px]" : "h-8 gap-0.5"}`}>
            <div className="w-1 h-full bg-black/80"></div>
            <div className="w-0.5 h-full bg-black/80"></div>
            <div className="w-1.5 h-full bg-black/80"></div>
            <div className="w-0.5 h-full bg-black/80"></div>
            <div className="w-1 h-3/4 bg-black/80"></div>
            <div className="w-2 h-full bg-black/80"></div>
            <div className="w-0.5 h-full bg-black/80"></div>
            <div className="w-1 h-full bg-black/80"></div>
            <div className="w-0.5 h-3/4 bg-black/80"></div>
            <div className="w-1 h-full bg-black/80"></div>
        </div>
    );
}

export function GridLines() {
    return (
        <div className="absolute inset-0 z-0 opacity-10 pointer-events-none"
            style={{ backgroundImage: 'linear-gradient(rgba(0,0,0,1) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,1) 1px, transparent 1px)', backgroundSize: '20px 20px' }}
        />
    );
}
