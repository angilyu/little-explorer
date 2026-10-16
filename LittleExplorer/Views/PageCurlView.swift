import SwiftUI
import UIKit

/// A page-curl book built on UIPageViewController. Each page is a SwiftUI view.
struct PageCurlView: UIViewControllerRepresentable {
    let pageCount: Int
    @Binding var currentIndex: Int
    let makePage: (Int) -> AnyView

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIViewController(context: Context) -> UIPageViewController {
        let controller = UIPageViewController(
            transitionStyle: .pageCurl,
            navigationOrientation: .horizontal,
            options: [.spineLocation: NSNumber(value: UIPageViewController.SpineLocation.min.rawValue)]
        )
        controller.isDoubleSided = false
        controller.dataSource = context.coordinator
        controller.delegate = context.coordinator
        controller.view.backgroundColor = .clear
        controller.setViewControllers([context.coordinator.page(at: currentIndex)], direction: .forward, animated: false)
        return controller
    }

    func updateUIViewController(_ controller: UIPageViewController, context: Context) {
        let coordinator = context.coordinator
        coordinator.parent = self
        guard !coordinator.isAnimating,
              let visible = controller.viewControllers?.first as? PageHost,
              visible.index != currentIndex else { return }
        let direction: UIPageViewController.NavigationDirection = currentIndex > visible.index ? .forward : .reverse
        coordinator.isAnimating = true
        controller.setViewControllers([coordinator.page(at: currentIndex)], direction: direction, animated: true) { _ in
            coordinator.isAnimating = false
        }
    }

    final class PageHost: UIHostingController<AnyView> {
        var index = 0
    }

    @MainActor
    final class Coordinator: NSObject, UIPageViewControllerDataSource, UIPageViewControllerDelegate {
        var parent: PageCurlView
        var isAnimating = false
        private var cache: [Int: PageHost] = [:]

        init(parent: PageCurlView) { self.parent = parent }

        func page(at index: Int) -> PageHost {
            if let host = cache[index] { return host }
            let host = PageHost(rootView: parent.makePage(index))
            host.index = index
            host.view.backgroundColor = UIColor(Theme.paper)
            cache[index] = host
            return host
        }

        func pageViewController(_ pageViewController: UIPageViewController, viewControllerBefore viewController: UIViewController) -> UIViewController? {
            guard let host = viewController as? PageHost, host.index > 0 else { return nil }
            return page(at: host.index - 1)
        }

        func pageViewController(_ pageViewController: UIPageViewController, viewControllerAfter viewController: UIViewController) -> UIViewController? {
            guard let host = viewController as? PageHost, host.index < parent.pageCount - 1 else { return nil }
            return page(at: host.index + 1)
        }

        func pageViewController(_ pageViewController: UIPageViewController, didFinishAnimating finished: Bool, previousViewControllers: [UIViewController], transitionCompleted completed: Bool) {
            guard completed, let host = pageViewController.viewControllers?.first as? PageHost else { return }
            if parent.currentIndex != host.index { parent.currentIndex = host.index }
        }
    }
}
