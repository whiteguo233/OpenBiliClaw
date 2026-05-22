//
//  SafariWebExtensionHandler.swift
//  OpenBiliClaw Extension
//
//  Native message handler for the Safari Web Extension. The OpenBiliClaw
//  extension is fully self-contained in JavaScript and currently does not
//  use native messaging, but the principal class is required by Safari
//  for any Web Extension. If the JS side ever calls
//  ``browser.runtime.sendNativeMessage`` this is where the request lands.
//

import SafariServices
import os.log

class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {

    func beginRequest(with context: NSExtensionContext) {
        let request = context.inputItems.first as? NSExtensionItem
        let message = request?.userInfo?[SFExtensionMessageKey]

        os_log(.default, "Received message from browser.runtime.sendNativeMessage: %@",
               String(describing: message))

        // No-op handler. Echo back an acknowledgement so the JS caller
        // doesn't time out.
        let response = NSExtensionItem()
        response.userInfo = [SFExtensionMessageKey: ["echo": message ?? NSNull()]]
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }
}
